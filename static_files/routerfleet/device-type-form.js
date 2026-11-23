/* The device type form: the wizard's steps, the parts shown by a choice, and
   trying the commands on a device. */
(function () {
    var form = document.getElementById('dt-form');
    function shell() {
        var mode = document.getElementById('session_mode');
        var on = mode && mode.value === 'shell';
        document.querySelectorAll('.rf-shell').forEach(function (e) { e.style.display = on ? '' : 'none'; });
        var vendor = document.getElementById('check_vendor');
        document.querySelectorAll('.rf-vendor').forEach(function (e) { e.style.display = vendor && vendor.checked ? '' : 'none'; });
    }
    ['session_mode', 'check_vendor'].forEach(function (id) { var e = document.getElementById(id); if (e) { e.addEventListener('change', shell); } });
    shell();

    // Wizard: one section at a time
    var sections = Array.prototype.slice.call(document.querySelectorAll('.rf-wizard section'));
    var step = 0;
    function show(n) {
        if (!sections.length) { return; }
        step = Math.max(0, Math.min(n, sections.length - 1));
        sections.forEach(function (s, i) { s.classList.toggle('active', i === step); });
        document.querySelectorAll('#steps li').forEach(function (l, i) { l.classList.toggle('active', i === step); });
        document.getElementById('prev').style.display = step ? '' : 'none';
        document.getElementById('next').style.display = step < sections.length - 1 ? '' : 'none';
        window.scrollTo(0, 0);
    }
    if (sections.length) {
        document.getElementById('next').addEventListener('click', function () { show(step + 1); });
        document.getElementById('prev').addEventListener('click', function () { show(step - 1); });
        document.querySelectorAll('#steps li').forEach(function (l, i) { l.addEventListener('click', function () { show(i); }); });
        show(0);
    }

    // Start from a shipped type: copies its settings and commands into the form
    var presets = JSON.parse(JSON.parse(document.getElementById('dt-presets').textContent));
    var start = document.getElementById('start');
    if (start) {
        start.addEventListener('change', function () {
            var p = presets[start.value];
            if (!p) { return; }
            Object.keys(p).forEach(function (field) {
                var e = document.getElementById(field);
                if (!e || field === 'name') { return; }
                if (e.type === 'checkbox') { e.checked = !!p[field]; } else if (typeof p[field] !== 'object') { e.value = p[field]; }
            });
            var inv = p.inventory_fields || {};
            document.querySelectorAll('[id^="inventory_"]').forEach(function (e) {
                var f = e.id.replace('inventory_', ''); if (f !== 'repeat') { e.value = (inv[f] || []).join('\n'); }
            });
            Object.keys(p.commands || {}).forEach(function (key) {
                var e = document.getElementById('command_' + key); if (e) { e.value = p.commands[key]; }
            });
            shell();
        });
    }

    // Try on a device
    var tryButton = document.getElementById('try-button');
    if (tryButton) {
        tryButton.addEventListener('click', function () {
            var data = new FormData(form);
            ['try_address', 'try_port', 'try_username', 'try_password', 'try_enable_password'].forEach(function (id) {
                data.append(id, document.getElementById(id).value);
            });
            var out = document.getElementById('try-result');
            out.textContent = 'Connecting...';
            tryButton.disabled = true;
            fetch('/system/device-types/try/', {method: 'POST', body: data, credentials: 'same-origin'})
                .then(function (r) { return r.json(); })
                .then(function (result) {
                    out.textContent = '';
                    if (result.message && !(result.steps || []).length) {
                        var m = document.createElement('div'); m.className = 'text-danger'; m.textContent = result.message; out.appendChild(m);
                    }
                    (result.steps || []).forEach(function (s) {
                        var box = document.createElement('div'); box.className = 'rf-try-step';
                        var head = document.createElement('div');
                        var mark = document.createElement('i'); mark.className = 'fas ' + (s.ok ? 'fa-check text-success' : 'fa-times text-danger') + ' me-1'; head.replaceChildren(mark);
                        var title = document.createElement('strong'); title.textContent = s.title; head.appendChild(title);
                        if (s.message) { head.appendChild(document.createTextNode(' - ' + s.message)); }
                        box.appendChild(head);
                        if (s.fields) {
                            var list = document.createElement('div'); list.className = 'small';
                            s.fields.forEach(function (f) { var row = document.createElement('div'); row.textContent = f[0] + ': ' + (f[1] || '-'); list.appendChild(row); });
                            box.appendChild(list);
                        }
                        if (s.output) { var pre = document.createElement('pre'); pre.textContent = s.output; box.appendChild(pre); }
                        out.appendChild(box);
                    });
                    if (result.message && (result.steps || []).length) {
                        var e = document.createElement('div'); e.className = 'text-danger'; e.textContent = result.message; out.appendChild(e);
                    }
                })
                .catch(function () { out.textContent = 'The try could not be run.'; })
                .finally(function () { tryButton.disabled = false; });
        });
    }
})();
