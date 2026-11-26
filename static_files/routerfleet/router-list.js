/* The device list: a list on the left, the chosen device in the inspector on
   the right. Search ('!' leaves out, '^' is a regular expression), the group
   and state filters, the selection and what can be done with it, the CSV
   export of what the list shows, and the online/offline dots kept current.
   A static file without jQuery; text only - a device name is never read as
   markup. On a narrow screen a device opens its own page instead. */
(function () {
  'use strict';

  function byId(id) { return document.getElementById(id); }
  function csrfToken() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ''; }
  function on(id, handler) {
    var element = byId(id);
    if (element) {
      element.addEventListener('click', function (event) { event.preventDefault(); handler(event, element); });
    }
  }
  function items() { return Array.prototype.slice.call(document.querySelectorAll('#device-list .rf-li')); }
  function shown() { return items().filter(function (item) { return !item.hidden; }); }

  /* --- Search and filters ------------------------------------------------- */
  function matcher(value) {
    var excludes = value.charAt(0) === '!';
    var term = (excludes ? value.slice(1) : value).trim();
    if (!term) { return function () { return true; }; }
    var test;
    if (term.charAt(0) === '^') {
      var expression;
      try { expression = new RegExp(term.slice(1), 'i'); } catch (error) { return null; }   // half typed: keep the list
      test = function (text) { return expression.test(text); };
    } else {
      var lower = term.toLowerCase();
      test = function (text) { return text.toLowerCase().indexOf(lower) > -1; };
    }
    return excludes ? function (text) { return !test(text); } : test;
  }
  function applyFilters() {
    var input = byId('device-search');
    var match = matcher(input ? input.value : '');
    if (input) { input.classList.toggle('is-invalid', match === null); }
    if (match === null) { return; }
    var state = byId('device-state') ? byId('device-state').value : '';
    var count = 0;
    items().forEach(function (item) {
      var fits = match(item.dataset.search || '') &&
        (!state || item.dataset.state === state || (' ' + (item.dataset.flags || '') + ' ').indexOf(' ' + state + ' ') > -1);
      item.hidden = !fits;
      if (fits) { count += 1; } else { var box = item.querySelector('.router-checkbox'); if (box) { box.checked = false; } }
    });
    var none = byId('device-none');
    if (none) { none.classList.toggle('d-none', count > 0 || !items().length); }
    refreshActions();
  }
  function setupFilters() {
    var input = byId('device-search');
    if (input) { input.addEventListener('input', applyFilters); }
    var state = byId('device-state');
    if (state) { state.addEventListener('change', applyFilters); }
    var group = byId('device-group');
    if (group) {
      group.addEventListener('change', function () {
        window.location.href = '/router/list/?filter_group=' + encodeURIComponent(group.value);
      });
    }
    // '/' jumps to the search, as in most lists
    document.addEventListener('keydown', function (event) {
      if (event.key === '/' && input && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
        event.preventDefault(); input.focus();
      }
    });
  }

  /* --- The inspector ------------------------------------------------------ */
  function wide() { return window.matchMedia('(min-width: 992px)').matches; }
  function inspect(uuid, item) {
    var holder = byId('rf-inspector');
    if (!holder) { return; }
    holder.classList.add('rf-loading');
    fetch('/router/inspect/?uuid=' + encodeURIComponent(uuid), {credentials: 'same-origin'})
      .then(function (response) { if (!response.ok) { throw new Error(response.status); } return response.text(); })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, 'text/html').querySelector('.rf-insp');
        if (!fresh) { throw new Error('empty'); }
        holder.replaceChildren(document.importNode(fresh, true));
        if (window.rfNameButtons) { window.rfNameButtons(holder); }
        items().forEach(function (other) { other.classList.toggle('on', other === item); });
        var url = new URL(window.location.href);
        url.searchParams.set('device', uuid);
        window.history.replaceState(null, '', url.pathname + url.search);
      })
      .catch(function () { window.location.href = '/router/details/?uuid=' + encodeURIComponent(uuid); })
      .then(function () { holder.classList.remove('rf-loading'); });
  }
  function setupInspector() {
    document.addEventListener('click', function (event) {
      var link = event.target.closest && event.target.closest('[data-inspect]');
      if (!link || !wide() || event.metaKey || event.ctrlKey || event.shiftKey || event.button) { return; }
      event.preventDefault();
      inspect(link.dataset.inspect, link.closest('.rf-li'));
    });
    // Up and down move through the list shown
    document.addEventListener('keydown', function (event) {
      if (!wide() || (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')) { return; }
      if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.id !== 'device-search') { return; }
      var list = shown();
      if (!list.length) { return; }
      var index = list.findIndex(function (item) { return item.classList.contains('on'); });
      var next = list[Math.max(0, Math.min(list.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))];
      if (next && next !== list[index]) {
        event.preventDefault();
        inspect(next.dataset.uuid, next);
        next.scrollIntoView({block: 'nearest'});
      }
    });
  }

  /* --- CSV: the devices the list shows, every field ---------------------- */
  // A cell that starts like a formula is written as text, so a device named
  // =HYPERLINK(...) can not run anything in a spreadsheet.
  function csvCell(text) {
    var value = String(text || '').replace(/\s+/g, ' ').trim();
    if (/^[=+\-@\t\r]/.test(value)) { value = "'" + value; }
    return '"' + value.replace(/"/g, '""') + '"';
  }
  var CSV = [['Name', null], ['Type', 'csvType'], ['Address', 'csvAddress'], ['State', null], ['Groups', 'csvGroups'],
             ['Backup profile', 'csvProfile'], ['Last backup', 'csvLastBackup'], ['Last configuration change', 'csvLastChange'],
             ['OS version', 'csvOs'], ['Available version', 'csvAvailable'], ['Model', 'csvModel'],
             ['Serial number', 'csvSerial'], ['Bootloader', 'csvFirmware'], ['Architecture', 'csvArchitecture']];
  var STATES = {online: 'Online', offline: 'Offline', disabled: 'Disabled', unmonitored: 'Not monitored'};
  function exportCsv() {
    var lines = [CSV.map(function (c) { return csvCell(c[0]); }).join(',')];
    shown().forEach(function (item) {
      lines.push(CSV.map(function (c) {
        if (c[0] === 'Name') { return csvCell(item.querySelector('.rf-li-name').firstChild.textContent); }
        if (c[0] === 'State') { return csvCell(STATES[item.dataset.state] || ''); }
        return csvCell(item.dataset[c[1]]);
      }).join(','));
    });
    var blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], {type: 'text/csv;charset=utf-8'});
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'device-list-' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }

  /* --- The selection and what can be done with it ------------------------ */
  function boxes() { return Array.prototype.slice.call(document.querySelectorAll('#device-list .router-checkbox')); }
  function selected() { return boxes().filter(function (b) { return b.checked; }).map(function (b) { return b.dataset.uuid; }); }
  function refreshActions() {
    var count = selected().length;
    var bar = byId('bulk-actions');
    if (bar) { bar.classList.toggle('d-none', !count); }
    var label = byId('selected-count');
    if (label) { label.textContent = count ? count + ' selected' : 'Select'; }
    var all = byId('select-all');
    var visible = shown().length;
    if (all) { all.checked = count > 0 && count === visible; all.indeterminate = count > 0 && count < visible; }
  }
  function setAll(checked) {
    boxes().forEach(function (b) { b.checked = checked && !b.closest('.rf-li').hidden; });
    refreshActions();
  }

  function postJson(url, routers, extra) {
    var body = new URLSearchParams();
    routers.forEach(function (uuid) { body.append('routers[]', uuid); });
    Object.keys(extra || {}).forEach(function (key) { body.append(key, extra[key]); });
    body.append('csrfmiddlewaretoken', csrfToken());
    return fetch(url, {method: 'POST', body: body, credentials: 'same-origin',
                       headers: {'X-CSRFToken': csrfToken(), 'Accept': 'application/json'}})
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) { throw new Error(data.error || 'The request failed.'); }
          return data;
        });
      });
  }

  // The selection goes in the body of a POST, never in the URL: a large
  // selection would exceed the longest request line the server accepts
  function postSelected(url) {
    var routers = selected();
    if (!routers.length) { return; }
    var form = document.createElement('form');
    form.method = 'post';
    form.action = url;
    form.className = 'd-none';
    routers.concat(['']).forEach(function (uuid, i) {
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = i < routers.length ? 'routers[]' : 'csrfmiddlewaretoken';
      input.value = i < routers.length ? uuid : csrfToken();
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  }

  function setupSelection() {
    var all = byId('select-all');
    if (all) { all.addEventListener('change', function () { setAll(all.checked); }); }
    document.addEventListener('change', function (event) {
      if (event.target.classList && event.target.classList.contains('router-checkbox')) { refreshActions(); }
    });
    on('update-information', function () {
      var routers = selected();
      if (!routers.length) { return; }
      postJson('/router/update_information/multiple/', routers).then(function (response) {
        rfNotify('success', 'Information update started', 'Updating information for ' + response.requested +
                 ' device(s). The list will show the new values shortly.');
      }).catch(function (error) { rfNotify('danger', 'Error', error.message); })
        .then(function () { setAll(false); });
    });
    on('create-backup', function () {
      var routers = selected();
      if (!routers.length) { return; }
      postJson('/router/create_instant_backup/multiple/', routers).then(function (response) {
        var started = [], problems = [];
        (response.results || []).forEach(function (item) {
          if (!item.status) { started.push(item.router); } else { problems.push(item.router + ': ' + item.status); }
        });
        if (started.length) { rfNotify('success', 'Backup started', started.join('\n')); }
        if (problems.length) { rfNotify('danger', 'Backup problems', problems.join('\n')); }
      }).catch(function (error) { rfNotify('danger', 'Error', error.message); })
        .then(function () { setAll(false); });
    });
    on('manage-groups', function () { postSelected('/router/manage_groups/multiple/'); });
    on('update-firmware', function () { postSelected('/system/firmware/update/'); });
    on('update-bootloader', function () { postSelected('/system/firmware/update/?kind=bootloader'); });
    on('run-command', function () { postSelected('/fleet_commander/command/execute_multiple/'); });
    on('edit-multiple', function () { postSelected('/router/edit/multiple/'); });
    // Enable / disable: a disabled device is neither backed up nor monitored
    function send(routers, enable) {
      postJson('/router/enable/multiple/', routers, {action: enable ? 'enable' : 'disable'})
        .then(function (data) {
          rfNotify('success', enable ? 'Devices enabled' : 'Devices disabled', data.changed + ' device(s) changed. The list is reloaded.');
          setTimeout(function () { window.location.reload(); }, 900);
        })
        .catch(function (error) { rfNotify('danger', 'Error', error.message); });
    }
    on('enable-multiple', function () { var routers = selected(); if (routers.length) { send(routers, true); } });
    on('disable-multiple', function (event, element) {
      var routers = selected();
      if (!routers.length) { return; }
      var anchor = byId('bulk-actions').querySelector('.dropdown-toggle') || element;
      window.rfConfirmInline(anchor, 'Disable ' + routers.length + ' device(s)? They are not backed up or monitored ' +
                             'until enabled again.', 'disable', false, 'Disable')
        .then(function (word) { if (word) { send(routers, false); } });
    });
  }

  /* --- Online / offline, as the monitoring sees it ----------------------- */
  function setupStatus(page) {
    var last = page.dataset.lastStatusChange || '';
    var group = page.dataset.group || '';
    function getJson(url) {
      return fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
        .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); });
    }
    function refresh() {
      var url = '/monitoring/export_router_list/' + (group ? '?filter_group=' + encodeURIComponent(group) : '');
      getJson(url).then(function (data) {
        var list = data.router_list || {};
        Object.keys(list).forEach(function (uuid) {
          var router = list[uuid];
          var dot = document.getElementById('status-' + uuid);
          if (!dot || !dot.dataset.monitored) { return; }
          var now = router.online ? 'online' : 'offline';
          var item = dot.closest('.rf-li');
          if (item.dataset.state === now) { return; }
          item.dataset.state = now;
          dot.className = 'rf-dot ' + (router.online ? 'ok' : 'bad');
          dot.title = router.online ? 'Online' : 'Offline';
          rfNotify(router.online ? 'success' : 'danger', 'Device status updated', 'Device ' + router.name + ' is now ' + now);
        });
        applyFilters();
      }).catch(function () {});
    }
    function check() {
      getJson('/monitoring/last_status_change/').then(function (data) {
        if (data.last_status_change !== last) {
          last = data.last_status_change;
          refresh();
        }
      }).catch(function () {});
    }
    check();
    setInterval(check, 30000);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var page = byId('router-list-page');
    if (!page) { return; }
    setupFilters();
    setupInspector();
    setupSelection();
    on('export-csv', exportCsv);
    setupStatus(page);
    var chosen = document.querySelector('#device-list .rf-li.on');
    if (chosen) { chosen.scrollIntoView({block: 'nearest'}); }
  });
})();
