"""containers/cron/rf-update, run against stand-ins for docker and cosign that
record every call. Needs sh and jq (present on the CI runners)."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'containers' / 'cron' / 'rf-update'
REG = 'ghcr.io/alessiobravi'

DOCKER = r'''#!/bin/sh
echo "docker $*" >> "$STUB/calls"
case "$1" in
  version) exit 0 ;;
  ps) echo c1; echo c2; echo c3; echo c4; exit 0 ;;
  pull|tag|rmi|rm|run) exit 0 ;;
  exec)
    case "$*" in *pg_dump*) echo DUMP; exit 0 ;; *pg_restore*) cat > "$STUB/restored"; exit 0 ;; esac; exit 0 ;;
  compose)
    case "$*" in
      *"config --services"*) printf 'routerfleet-postgres\nrouterfleet\nrouterfleet-cron\nrouterfleet-monitoring\n'; exit 0 ;;
      *" up "*) touch "$STUB/updated"; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  image)
    # image inspect -f TEMPLATE REF
    case "$5" in
      *:latest) [ -n "${STUB_NO_LATEST:-}" ] && exit 1; echo "sha256:old"; exit 0 ;;
    esac
    case "$4" in
      *RepoDigests*) img=${5%:*}; echo "$img@sha256:1111"; exit 0 ;;
      *image.version*) echo "${STUB_LABEL:-$STUB_TARGET}"; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  inspect)
    if [ "$2" != "-f" ]; then
      [ "$2" = routerfleet-postgres ] && [ -n "${STUB_PG:-}" ] && exit 0
      exit 1
    fi
    case "$3" in
      *compose.project.working_dir*) echo /srv/rf ;;
      *compose.project.config_files*) echo /srv/rf/docker-compose.yml ;;
      *compose.project\"*) echo "${STUB_PROJECT:-rf}" ;;
      *compose.service*) echo routerfleet-cron ;;
      *image.version*)
        if [ -e "$STUB/updated" ] && [ -z "${STUB_STAYS:-}" ]; then echo "$STUB_TARGET"; else echo "$STUB_RUNNING"; fi ;;
      *Health*) echo "${STUB_HEALTH:-healthy}" ;;
      *Config.Image*)
        case "$4" in
          c1) echo "$REG/routerfleet:latest" ;; c2) echo "$REG/routerfleet-cron:latest" ;;
          c3) echo "$REG/routerfleet-postgres:latest" ;; c4) echo "someone/else:latest" ;;
        esac ;;
    esac
    exit 0 ;;
esac
exit 0
'''.replace('$REG', REG)

COSIGN = '''#!/bin/sh
echo "cosign $*" >> "$STUB/calls"
[ -z "${STUB_BAD_SIGNATURE:-}" ]
'''


@unittest.skipUnless(shutil.which('jq') and shutil.which('sh'), 'needs jq and sh')
class UpdaterScriptTests(unittest.TestCase):

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix='rfupd-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stub = self.root / 'stub'; self.stub.mkdir()
        self.update = self.root / 'update'; self.update.mkdir()
        self.private = self.root / 'private'; self.private.mkdir()
        self.compose = self.root / 'compose'; self.compose.mkdir()
        (self.compose / 'docker-compose.yml').write_text('services: {}\n')
        (self.compose / '.env').write_text('X=1\n')
        self.socket = self.root / 'docker.sock'
        bin_dir = self.root / 'bin'; bin_dir.mkdir()
        for name, body in (('docker', DOCKER), ('cosign', COSIGN), ('sleep', '#!/bin/sh\nexit 0\n'),
                           ('flock', '#!/bin/sh\nexit 0\n')):
            (bin_dir / name).write_text(body); (bin_dir / name).chmod(0o755)
        self.env = {'STUB': str(self.stub), 'STUB_RUNNING': '0.20.0', 'STUB_TARGET': '0.21.0',
                    'RF_UPDATE_DIR': str(self.update), 'RF_UPDATE_PRIVATE_DIR': str(self.private),
                    'RF_COMPOSE_DIR': str(self.compose), 'RF_HEALTHY_WITHIN': '30',
                    'RF_UPDATE_PATH': f'{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(self.root)}

    def run_updater(self, request=None, raw=None, **env):
        if request is not None:
            raw = json.dumps(request)
        if raw is not None:
            (self.update / 'request.json').write_text(raw)
        script = SCRIPT.read_text().replace('[ ! -S /var/run/docker.sock ]', '[ ! -e /dev/null ]')
        runner = self.root / 'rf-update'; runner.write_text(script)
        result = subprocess.run(['sh', str(runner), 'run'], env={**self.env, **env}, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def calls(self):
        path = self.stub / 'calls'
        return path.read_text() if path.exists() else ''

    def status(self):
        return json.loads((self.update / 'status.json').read_text())

    def request(self, version='0.21.0', **extra):
        return {'id': 'a' * 32, 'version': version, 'approved_by': 'admin', 'approved_at': '2026-11-22T20:00:00Z', **extra}

    def test_heartbeat_without_a_request(self):
        self.run_updater()
        beat = json.loads((self.update / 'updater.json').read_text())
        self.assertTrue(beat['ready'], beat)
        self.assertEqual(beat['running'], '0.20.0')
        self.assertNotIn('pull', self.calls())

    def test_malformed_requests_are_refused_before_anything(self):
        for raw in (json.dumps(self.request(extra='x')), json.dumps(self.request(version='0.21.0;id')),
                    json.dumps(self.request(version='../../x')), '{"id":', 'x' * 5000,
                    json.dumps({**self.request(), 'approved_by': 'a b'}), json.dumps(self.request(version='٠.٢١.٠'))):
            with self.subTest(raw=raw[:40]):
                self.run_updater(raw=raw)
                self.assertEqual(self.status()['state'], 'refused')
                self.assertEqual((self.status()['id'], self.status()['version']), ('', ''), 'nothing of it is echoed')
                self.assertNotIn('docker pull', self.calls())
                self.assertFalse((self.update / 'request.json').exists(), 'consumed')

    def test_a_symlinked_request_is_refused(self):
        secret = self.root / 'secret'; secret.write_text(json.dumps(self.request()))
        os.symlink(secret, self.update / 'request.json')
        self.run_updater()
        self.assertEqual(self.status()['state'], 'refused')
        self.assertNotIn('docker pull', self.calls())

    def test_only_newer_releases(self):
        for version in ('0.20.0', '0.19.9'):
            with self.subTest(version=version):
                self.run_updater(self.request(version))
                self.assertEqual(self.status()['state'], 'refused')
                self.assertNotIn('docker pull', self.calls())

    def test_an_unsigned_image_changes_nothing(self):
        self.run_updater(self.request(), STUB_BAD_SIGNATURE='1')
        self.assertEqual(self.status()['state'], 'failed')
        self.assertIn('not signed', self.status()['message'])
        calls = self.calls()
        self.assertIn('cosign verify --certificate-identity-regexp', calls)
        self.assertNotIn('docker tag', calls, 'nothing re-tagged')
        self.assertNotIn('docker compose', calls, 'nothing restarted')
        self.assertNotIn('pg_dump', calls)

    def test_a_wrong_version_label_changes_nothing(self):
        self.run_updater(self.request(), STUB_LABEL='0.22.0')
        self.assertEqual(self.status()['state'], 'failed')
        self.assertNotIn(' up -d', self.calls())

    def test_a_signed_release_is_installed_scheduler_last(self):
        self.run_updater(self.request(), STUB_PG='1')
        self.assertEqual(self.status()['state'], 'done', self.status())
        calls = self.calls()
        # Exactly our images, verified by digest against the release workflow
        for image in ('routerfleet', 'routerfleet-cron', 'routerfleet-postgres'):
            self.assertIn(f'docker pull -q {REG}/{image}:0.21.0', calls)
            self.assertIn(f'{REG}/{image}@sha256:1111', calls)
        self.assertNotIn('someone/else', calls.replace('inspect -f {{ .Config.Image }}', ''))
        self.assertIn('build_and_push_images[.]yml@refs/heads/main', calls)
        self.assertIn('--certificate-oidc-issuer https://token.actions.githubusercontent.com', calls)
        # The database was backed up before anything restarted
        self.assertLess(calls.index('pg_dump'), calls.index(' up -d'))
        self.assertEqual((self.private / 'backup' / 'database.dump').read_text(), 'DUMP\n')
        up = [line for line in calls.splitlines() if ' up -d' in line][0]
        self.assertIn('routerfleet-postgres routerfleet routerfleet-monitoring', up)
        self.assertNotIn('routerfleet-cron', up, 'the scheduler is not restarted from inside itself')
        self.assertIn('-p rf --project-directory /srv/rf', up)
        self.assertIn('run -d --rm --name routerfleet-update-helper', calls)

    def test_not_ready_in_time_puts_everything_back(self):
        self.run_updater(self.request(), STUB_PG='1', STUB_HEALTH='unhealthy')
        self.assertEqual(self.status()['state'], 'rolled_back', self.status())
        calls = self.calls()
        self.assertIn(f'docker tag {REG}/routerfleet:rf-rollback {REG}/routerfleet:latest', calls)
        self.assertEqual((self.stub / 'restored').read_text(), 'DUMP\n', 'the backup went back in')
        self.assertIn('back on 0.20.0', self.status()['message'])
        self.assertNotIn('update-helper', calls)

    def test_an_unexpected_project_name_disables_the_updater(self):
        self.run_updater(self.request(), STUB_PROJECT='x;reboot')
        beat = json.loads((self.update / 'updater.json').read_text())
        self.assertFalse(beat['ready'])
        self.assertEqual(self.status()['state'], 'failed')
        self.assertNotIn('docker pull', self.calls())
