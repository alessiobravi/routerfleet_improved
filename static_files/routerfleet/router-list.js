/* The router list: status updates, search with '!', column filters, the
   columns shown, the CSV export and the actions on a selection. A static file
   without jQuery; text only - a router name is never read as markup. The
   page hands over its two values as data attributes of #router-list-page. */
(function () {
  'use strict';

  var COLUMNS = [
    {name: 'name', label: 'Name', default: true},
    {name: 'type', label: 'Type', default: true},
    {name: 'address', label: 'Address', default: false},
    {name: 'status', label: 'Status', default: true},
    {name: 'backup', label: 'Backup', default: true},
    {name: 'last_backup', label: 'Last Backup', default: false},
    {name: 'last_config_change', label: 'Last Config Change', default: false},
    {name: 'next_daily_backup', label: 'Next Daily Backup', default: false},
    {name: 'next_weekly_backup', label: 'Next Weekly Backup', default: false},
    {name: 'next_monthly_backup', label: 'Next Monthly Backup', default: false},
    {name: 'lock_information', label: 'Router Lock Information', default: false},
    {name: 'groups', label: 'Groups', default: true},
    {name: 'auth', label: 'Auth', default: false},
    {name: 'os_version', label: 'OS Version', default: false},
    {name: 'model_name', label: 'Model Name', default: false},
    {name: 'model_version', label: 'Model Version', default: false},
    {name: 'serial_number', label: 'Serial Number', default: false},
    {name: 'firmware_version', label: 'Firmware Version', default: false},
    {name: 'available_version', label: 'Available Version', default: true},
    {name: 'architecture', label: 'Architecture', default: false},
    {name: 'cpu', label: 'CPU', default: false}
  ];
  var ACTIONS = ['update-information', 'create-backup', 'run-command', 'manage-groups', 'edit-multiple'];

  function byId(id) { return document.getElementById(id); }
  function csrfToken() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ''; }
  function on(id, handler) {
    var element = byId(id);
    if (element) {
      element.addEventListener('click', function (event) { event.preventDefault(); handler(event, element); });
    }
  }

  function readCookie(name) {
    var found = document.cookie.split(';').map(function (c) { return c.trim(); })
      .filter(function (c) { return c.indexOf(name + '=') === 0; })[0];
    return found ? decodeURIComponent(found.slice(name.length + 1)) : null;
  }
  function writeCookie(name, value, days) {
    var date = new Date();
    date.setTime(date.getTime() + days * 86400000);
    document.cookie = name + '=' + encodeURIComponent(value) + '; expires=' + date.toUTCString() + '; path=/; SameSite=Lax';
  }

  /* --- Search: '!' excludes, '^' is a regular expression, '(empty)' blank -- */
  // A column without a value is not an empty cell in the page: this list
  // writes '---' where there is nothing, and the text DataTables reads out of
  // a cell has the tags taken out and its edges trimmed.
  var EMPTY_WORD = '(empty)';
  var EMPTY_VALUE = '\\s*(---)?\\s*$';

  // A pattern is only handed to DataTables once it compiles: half typed, '^(' is
  // an error, and the list is left as it is until the pattern is finished.
  // smart stays off: it would wrap the pattern in a lookahead per word.
  function regexSearch(searchable, pattern) {
    try { new RegExp(pattern); } catch (error) { return false; }
    searchable.search(pattern, true, false, true).draw();
    return true;
  }

  function applySearch(searchable, value, smart) {
    var excludes = value.charAt(0) === '!';
    var term = excludes ? value.slice(1) : value;
    if (term === '') {
      searchable.search('', false, false, true).draw();
      return true;
    }
    if (term.toLowerCase() === EMPTY_WORD) {
      return regexSearch(searchable, excludes ? '^(?!' + EMPTY_VALUE + ')' : '^' + EMPTY_VALUE);
    }
    if (term.charAt(0) === '^') {
      // The search box looks at the whole row, which starts with the empty
      // checkbox column: there the expression is looked for anywhere. A
      // column filter keeps the anchor: '^7' means the cell starts with a 7.
      var expression = term.slice(1);
      return regexSearch(searchable, excludes
        ? '^(?!' + (smart ? '.*' : '') + expression + ')'
        : (smart ? expression : '^' + expression));
    }
    if (excludes) {
      return regexSearch(searchable, '^(?!.*' + DataTable.util.escapeRegex(term) + ')');
    }
    // caseInsen explicitly: left out it counts as false
    searchable.search(term, false, smart, true).draw();
    return true;
  }

  // The search box of DataTables is replaced by a copy that understands '!'.
  // A copy carries none of DataTables' listeners, and what DataTables writes
  // back after a draw goes to the original, so the text the user typed stays.
  function setupGlobalSearch(table) {
    var original = table.table().container().querySelector('.dt-search input');
    if (!original) { return; }
    var input = original.cloneNode(true);
    original.replaceWith(input);
    input.addEventListener('input', function () {
      input.classList.toggle('is-invalid', applySearch(table, input.value, true) === false);
    });
  }

  /* --- One filter per column, in its header, remembered in the browser ---- */
  // A remembered filter names its column by data-column: the server decides
  // which columns the list has, so an index can mean another column next time.
  var FILTER_KEY = 'router_list_filters';
  function readFilterState() {
    var state;
    try { state = JSON.parse(window.localStorage.getItem(FILTER_KEY)) || {}; } catch (error) { state = {}; }
    return typeof state === 'object' && state !== null ? state : {};
  }

  function setupColumnFilters(table) {
    var fields = [];
    table.columns().every(function () {
      var index = this.index();
      var header = this.header();
      var name = header.getAttribute('data-column');
      if (!name) { return; }   // the checkbox and the edit column have nothing to filter
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'router-column-filter form-control form-control-sm';
      input.placeholder = 'Filter (! excludes, (empty) blank, ^ regex)';
      input.title = 'text finds it, !text excludes it, ^pattern is a regular expression, (empty) shows the rows without a value';
      // A click or a key in the header would sort the column instead
      ['click', 'keydown', 'keypress', 'mousedown'].forEach(function (type) {
        input.addEventListener(type, function (event) { event.stopPropagation(); });
      });
      input.addEventListener('input', function () {
        input.classList.toggle('is-invalid', applySearch(table.column(index), input.value, false) === false);
        save(true);
      });
      var box = document.createElement('div');
      box.className = 'router-column-filter-box d-none';
      box.appendChild(input);
      header.appendChild(box);
      fields.push({name: name, index: index, input: input, box: box});
    });

    function save(visible) {
      // A term of a column the list does not show now stays stored, so
      // showing that column again brings its filter back
      var terms = readFilterState().terms;
      if (typeof terms !== 'object' || terms === null) { terms = {}; }
      fields.forEach(function (field) {
        if (field.input.value) { terms[field.name] = field.input.value; } else { delete terms[field.name]; }
      });
      try { window.localStorage.setItem(FILTER_KEY, JSON.stringify({visible: visible, terms: terms})); } catch (error) {}
    }

    function setVisible(visible) {
      fields.forEach(function (field) { field.box.classList.toggle('d-none', !visible); });
      if (visible) { return; }
      // A hidden filter would keep the list filtered without showing why
      fields.forEach(function (field) { field.input.value = ''; field.input.classList.remove('is-invalid'); });
      table.columns().every(function () { this.search(''); });
      table.draw();
    }

    var toggle = byId('toggleColumnFilters');
    on('toggleColumnFilters', function () {
      var visible = !fields.length || fields[0].box.classList.contains('d-none');
      setVisible(visible);
      toggle.classList.toggle('active', visible);
      save(visible);
    });

    var remembered = readFilterState();
    if (remembered.visible) {
      setVisible(true);
      if (toggle) { toggle.classList.add('active'); }
      var terms = remembered.terms || {};
      fields.forEach(function (field) {
        var value = terms[field.name];
        if (!value) { return; }
        field.input.value = value;
        field.input.classList.toggle('is-invalid', applySearch(table.column(field.index), value, false) === false);
      });
    }
  }

  /* --- The columns shown: a cookie the server reads ---------------------- */
  function setupColumnChoice() {
    var modal = byId('columnVisibilityModal');
    var holder = byId('columnCheckboxes');
    if (!modal || !holder) { return; }
    function visibleColumns() {
      var stored = readCookie('router_visible_columns');
      if (stored) {
        try { return JSON.parse(stored); } catch (error) {}
      }
      return COLUMNS.filter(function (c) { return c.default; }).map(function (c) { return c.name; });
    }
    on('showHideColumnsBtn', function () {
      var shown = visibleColumns();
      holder.replaceChildren.apply(holder, COLUMNS.map(function (column) {
        var row = document.createElement('div');
        row.className = 'form-check rf-checkbox';
        var input = document.createElement('input');
        input.className = 'form-check-input';
        input.type = 'checkbox';
        input.id = 'col-' + column.name;
        input.value = column.name;
        input.checked = shown.indexOf(column.name) > -1;
        var label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = input.id;
        label.textContent = column.label;
        row.append(input, label);
        return row;
      }));
      bootstrap.Modal.getOrCreateInstance(modal).show();
    });
    on('applyColumnVisibility', function () {
      var chosen = Array.prototype.map.call(holder.querySelectorAll('input:checked'), function (i) { return i.value; });
      writeCookie('router_visible_columns', JSON.stringify(chosen), 365);
      window.location.reload();
    });
    on('resetColumnVisibility', function () {
      document.cookie = 'router_visible_columns=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT';
      window.location.reload();
    });
  }

  /* --- CSV: the rows the search shows, the columns of the list ----------- */
  // A cell that starts like a formula is written as text, so a router named
  // =HYPERLINK(...) can not run anything in a spreadsheet.
  function csvCell(text) {
    var value = String(text).replace(/\s+/g, ' ').trim();
    if (/^[=+\-@\t\r]/.test(value)) { value = "'" + value; }
    return '"' + value.replace(/"/g, '""') + '"';
  }
  function exportCsv(table) {
    var columns = [];
    table.columns().every(function () {
      if (this.header().getAttribute('data-column')) { columns.push(this.index()); }
    });
    var lines = [columns.map(function (i) {
      return csvCell(table.column(i).header().querySelector('.dt-column-title')
        ? table.column(i).header().querySelector('.dt-column-title').textContent
        : table.column(i).header().firstChild.textContent);
    }).join(',')];
    table.rows({search: 'applied', order: 'applied'}).every(function () {
      var row = this.index();
      lines.push(columns.map(function (i) { return csvCell(table.cell(row, i).node().textContent); }).join(','));
    });
    var blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], {type: 'text/csv;charset=utf-8'});
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'router-list-' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }

  /* --- The selection and what can be done with it ------------------------ */
  // A filtered out row is not in the page, so the rows come from the table:
  // select all selects what the search shows, across all of its pages.
  function checkboxes(table, rows) {
    var boxes = [];
    table.rows(rows || {}).nodes().toArray().forEach(function (row) {
      var box = row.querySelector('.router-checkbox');
      if (box) { boxes.push(box); }
    });
    return boxes;
  }
  function selected(table) {
    return checkboxes(table).filter(function (b) { return b.checked; }).map(function (b) { return b.dataset.uuid; });
  }
  function refreshActions(table) {
    var any = selected(table).length > 0;
    ACTIONS.forEach(function (id) { var b = byId(id); if (b) { b.classList.toggle('d-none', !any); } });
  }
  function setAll(table, checked) {
    checkboxes(table, checked ? {search: 'applied'} : {}).forEach(function (b) { b.checked = checked; });
    refreshActions(table);
  }

  function postJson(url, routers) {
    var body = new URLSearchParams();
    routers.forEach(function (uuid) { body.append('routers[]', uuid); });
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
  function postSelected(table, url) {
    var routers = selected(table);
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

  function setupSelection(table) {
    on('select-all', function () { setAll(table, true); });
    on('select-none', function () { setAll(table, false); });
    document.addEventListener('change', function (event) {
      if (event.target.classList && event.target.classList.contains('router-checkbox')) { refreshActions(table); }
    });
    on('update-information', function () {
      var routers = selected(table);
      if (!routers.length) { return; }
      postJson('/router/update_information/multiple/', routers).then(function (response) {
        rfNotify('success', 'Information update started', 'Updating information for ' + response.requested +
                 ' router(s). The list will show the new values shortly.');
      }).catch(function (error) { rfNotify('danger', 'Error', error.message); })
        .then(function () { setAll(table, false); });
    });
    on('create-backup', function () {
      var routers = selected(table);
      if (!routers.length) { return; }
      postJson('/router/create_instant_backup/multiple/', routers).then(function (response) {
        var started = [], problems = [];
        (response.results || []).forEach(function (item) {
          if (!item.status) { started.push(item.router); } else { problems.push(item.router + ': ' + item.status); }
        });
        if (started.length) { rfNotify('success', 'Backup started', started.join('\n')); }
        if (problems.length) { rfNotify('danger', 'Backup problems', problems.join('\n')); }
      }).catch(function (error) { rfNotify('danger', 'Error', error.message); })
        .then(function () { setAll(table, false); });
    });
    on('manage-groups', function () { postSelected(table, '/router/manage_groups/multiple/'); });
    on('run-command', function () { postSelected(table, '/fleet_commander/command/execute_multiple/'); });
    on('edit-multiple', function () { postSelected(table, '/router/edit/multiple/'); });
  }

  /* --- Online / offline, as the monitoring sees it ----------------------- */
  function statusContent(online) {
    var word = document.createElement('span');
    word.className = 'd-none';
    word.textContent = online ? 'online' : 'offline';
    var icon = document.createElement('i');
    icon.className = online ? 'far fa-check-circle text-success' : 'far fa-times-circle text-danger';
    icon.title = online ? 'Host is Online' : 'Host is unavailable';
    return [word, icon];
  }
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
          var cell = document.getElementById('status-' + uuid);
          if (!cell) { return; }
          var word = cell.querySelector('span');
          var now = router.online ? 'online' : 'offline';
          if ((word ? word.textContent.trim() : '') === now) { return; }
          cell.replaceChildren.apply(cell, statusContent(router.online));
          if (window.dataTable) { window.dataTable.cell(cell).invalidate(); }
          rfNotify(router.online ? 'success' : 'danger', 'Host status updated', 'Host ' + router.name + ' is now ' + now);
        });
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
    var tableElement = page && page.querySelector('table.datatables-no-export');
    if (!tableElement || !window.DataTable || !DataTable.isDataTable(tableElement)) { return; }
    var table = new DataTable.Api(tableElement);
    setupColumnFilters(table);
    setupGlobalSearch(table);
    setupColumnChoice();
    setupSelection(table);
    on('export-csv', function () { exportCsv(table); });
    setupStatus(page);
  });
})();
