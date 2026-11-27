-- Grows a RouterFleet database to about :target_mb MB, for timing the move
-- from PostgreSQL 16 to 18 (.github/workflows/migration-timing.yml).
-- Every row is cloned from template rows the app created, so every column the
-- app expects is filled; only the text and the unique identifiers change.
-- The texts are device configurations in shape (repeated keywords, varying
-- names and addresses), so they compress about as a real backup does.

\set ON_ERROR_STOP on
-- psql does not fill variables inside the $-quoted blocks: handed over this way
SELECT set_config('rf.target_mb', :'target_mb', false);

CREATE FUNCTION pg_temp.rf_config(seed bigint) RETURNS text LANGUAGE sql VOLATILE AS $f$
    SELECT string_agg(format(
        '/ip firewall filter add action=%s chain=%s comment="rule %s for %s" dst-address=10.%s.%s.0/24 src-address-list=%s',
        (ARRAY['accept', 'drop', 'jump', 'log'])[1 + (i % 4)], (ARRAY['input', 'forward', 'output'])[1 + (i % 3)],
        i, substr(md5(seed::text || ':' || i), 1, 10), (seed + i) % 256, (i * 7) % 256,
        substr(md5(random()::text), 1, 12)), E'\n')
    FROM generate_series(1, 250) AS i
$f$;

-- n copies of every row of a table, with some columns replaced (uuid columns
-- always get a new value)
CREATE PROCEDURE pg_temp.rf_clone(tbl text, n int, overrides jsonb) LANGUAGE plpgsql AS $p$
DECLARE cols text; sel text;
BEGIN
    SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position),
           string_agg(CASE WHEN overrides ? column_name THEN overrides ->> column_name
                           WHEN data_type = 'uuid' THEN 'gen_random_uuid()'
                           ELSE 't.' || quote_ident(column_name) END, ', ' ORDER BY ordinal_position)
      INTO cols, sel
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = tbl AND column_name <> 'id';
    EXECUTE format('INSERT INTO %I (%s) SELECT %s FROM (SELECT * FROM %I WHERE id IN (SELECT min(id) FROM %I GROUP BY %s)) AS t CROSS JOIN generate_series(1, %s) AS g',
                   tbl, cols, sel, tbl, tbl, CASE WHEN tbl = 'audit_log_changerecord' THEN 'id' ELSE 'router_id' END, n);
END $p$;

-- Backups: the size. One batch per round - a config for every device - until
-- the database reaches the target
DO $d$
DECLARE target bigint := current_setting('rf.target_mb')::bigint * 1024 * 1024;
BEGIN
    WHILE pg_database_size(current_database()) < target LOOP
        CALL pg_temp.rf_clone('backup_data_routerbackup', 20, jsonb_build_object(
            'backup_text', 'pg_temp.rf_config(g + t.router_id * 100000 + (random() * 1e9)::bigint)',
            'backup_text_hash', 'md5(random()::text) || md5(random()::text)',
            'schedule_time', 't.schedule_time - make_interval(days => g)',
            'finish_time', 't.finish_time - make_interval(days => g)',
            'config_change_detected', 'true'));
    END LOOP;
END $d$;

-- Many small rows besides: the audit log and the availability history of
-- years of use (a count of every table is part of the move)
CALL pg_temp.rf_clone('audit_log_changerecord', :audit_rows, jsonb_build_object(
    'created', 't.created - make_interval(mins => g)'));
CALL pg_temp.rf_clone('monitoring_routerdowntime', :downtime_per_device, jsonb_build_object(
    'start_time', 'now() - make_interval(hours => g * 7)',
    'end_time', 'now() - make_interval(hours => g * 7) + interval ''3 minutes''',
    'total_down_time', '180'));

ANALYZE;
SELECT 'database ' || pg_size_pretty(pg_database_size(current_database())) AS size,
       (SELECT count(*) FROM backup_data_routerbackup) AS backups,
       (SELECT count(*) FROM audit_log_changerecord) AS audit_rows,
       (SELECT count(*) FROM monitoring_routerdowntime) AS downtime_rows;
