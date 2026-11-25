/* RouterFleet Improved: what every page needs, on AdminLTE 4 and Bootstrap 5.3.
   A static file and no jQuery: the Content-Security-Policy allows no inline
   script, and nothing here writes markup from data - text only. */
(function () {
  'use strict';

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  // Every action that changes something is sent as a POST with the CSRF
  // token, so only a page of this installation can trigger it - a link or an
  // image on a foreign page cannot.
  window.postAction = function (url, fields) {
    var form = document.createElement('form');
    form.method = 'post';
    form.action = url;
    var values = Object.assign({csrfmiddlewaretoken: csrfToken()}, fields || {});
    Object.keys(values).forEach(function (name) {
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = values[name];
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  };

  window.setCookie = function (name, value, days) {
    var expires = '';
    if (days) {
      var date = new Date();
      date.setTime(date.getTime() + days * 86400000);
      expires = '; expires=' + date.toUTCString();
    }
    document.cookie = name + '=' + (value || '') + expires + '; path=/; SameSite=Lax';
  };
  window.getCookie = function (name) {
    var found = document.cookie.split(';').map(function (c) { return c.trim(); })
      .filter(function (c) { return c.indexOf(name + '=') === 0; })[0];
    return found ? found.slice(name.length + 1) : null;
  };

  /* --- Tables (DataTables 3) ------------------------------------------- */
  function adjustTables() {
    if (window.DataTable) {
      DataTable.tables({visible: true, api: true}).columns.adjust().responsive.recalc();
    }
  }
  window.rfAdjustTables = adjustTables;

  // Also called for a part of the page that live updates replaced
  window.rfInitTables = function (root) {
    if (!window.DataTable) { return; }
    var scope = root || document;
    var tables = [];
    if (scope.matches && scope.matches('.datatables-no-export')) { tables.push(scope); }
    scope.querySelectorAll('.datatables-no-export').forEach(function (t) { tables.push(t); });
    var pageLength = parseInt(document.body.dataset.pageSize || '10', 10);
    tables.forEach(function (table) {
      if (DataTable.isDataTable(table)) { return; }
      window.dataTable = new DataTable(table, {
        paging: true,
        pageLength: pageLength,
        lengthMenu: [10, 25, 50, 100],
        searching: true,
        ordering: true,
        order: [],
        info: true,
        autoWidth: false,
        // The wording and the pager of the release before
        pagingType: 'simple_numbers',
        language: {lengthMenu: 'Show _MENU_ entries', paginate: {previous: 'Previous', next: 'Next'}},
        // On a narrow screen the columns that do not fit are left out instead
        // of folding behind a "+": the name (which opens the details), the
        // selection and the quick actions always stay on the row; a header
        // with data-priority says what to keep next
        responsive: {details: false},
        columnDefs: [
          {orderable: false, targets: [0, -1]},
          {className: 'all', targets: [0, -1]},
          {responsivePriority: 1, targets: 1}
        ]
      });
      table.classList.add('rf-table-ready');
    });
    // Measured while hidden, the columns do not fold on a phone: measure again
    adjustTables();
  };

  // Detail panels of object pages start collapsed. What the user opens is
  // remembered per page for the session, so a live update - which brings
  // the panels back from the server - does not close them again.
  window.rfInitPanels = function (root) {
    var store = {
      get: function (k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
      set: function (k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }
    };
    (root || document).querySelectorAll('.rf-o-details .card').forEach(function (card) {
      var head = card.querySelector(':scope > .card-header');
      if (!head || card.dataset.rfPanel) { return; }
      card.dataset.rfPanel = '1';
      var title = (head.querySelector('.card-title') || head).textContent.trim().slice(0, 60);
      var key = 'rf-panel:' + window.location.pathname + ':' + title;
      head.classList.add('rf-panel-head');
      head.setAttribute('role', 'button');
      head.setAttribute('tabindex', '0');
      var chevron = document.createElement('i');
      chevron.className = 'fas fa-chevron-down rf-chevron';
      chevron.setAttribute('aria-hidden', 'true');
      (head.querySelector('.card-title') || head).appendChild(chevron);
      function apply(open) {
        card.classList.toggle('rf-collapsed', !open);
        head.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) { adjustTables(); }
      }
      apply(store.get(key) === 'open');
      function toggle(event) {
        if (event.target.closest('a, button, input, select, textarea, label')) { return; }
        var open = card.classList.contains('rf-collapsed');
        store.set(key, open ? 'open' : 'closed');
        apply(open);
      }
      head.addEventListener('click', toggle);
      head.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(event); }
      });
    });
  };

  /* --- Selects: Tom Select where Chosen was ---------------------------- */
  window.rfInitSelects = function (root) {
    if (!window.TomSelect) { return; }
    (root || document).querySelectorAll('select.select, select.selectmultiple').forEach(function (select) {
      if (select.tomselect) { return; }
      new TomSelect(select, {plugins: select.multiple ? ['remove_button'] : [], maxOptions: null,
                             create: false, allowEmptyOption: true});
    });
  };

  /* --- The generic popup ----------------------------------------------- */
  function modalParts() {
    return {
      element: document.getElementById('genericModal'),
      label: document.getElementById('genericModalLabel'),
      body: document.getElementById('genericModalBody'),
      close: document.getElementById('genericModalCloseButton'),
      action: document.getElementById('genericModalActionButton')
    };
  }
  function paragraph(text, muted) {
    var p = document.createElement('p');
    if (muted !== false) { p.className = 'text-body-secondary'; }
    p.textContent = text;
    return p;
  }
  function openModal(title) {
    var m = modalParts();
    m.label.textContent = title;
    m.body.replaceChildren(paragraph('Loading…'));
    m.close.textContent = 'Close';
    m.action.classList.add('d-none');
    bootstrap.Modal.getOrCreateInstance(m.element).show();
    return m;
  }
  function getJson(url) {
    return fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) { throw new Error(data.error || 'The change could not be loaded.'); }
          return data;
        });
      });
  }

  // diff2html is only needed when somebody asks what changed, so it is
  // fetched on the first click instead of on every page
  var diffState = 'not loaded', diffWaiting = [];
  function withDiff2Html(callback) {
    if (diffState === 'loaded') { callback(); return; }
    diffWaiting.push(callback);
    if (diffState === 'loading') { return; }
    diffState = 'loading';
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/plugins/diff2html/diff2html.min.css';
    document.head.appendChild(css);
    var script = document.createElement('script');
    script.src = '/static/plugins/diff2html/diff2html.min.js';
    script.onload = function () { diffState = 'loaded'; diffWaiting.splice(0).forEach(function (f) { f(); }); };
    document.head.appendChild(script);
  }

  // What a configuration change was, in the generic popup. Everything that
  // shows such a change links to it with class="config-change-link" and the
  // uuid of the backup in data-uuid, so no page leaves the page.
  function showConfigChange(uuid) {
    var m = openModal('Configuration change');
    getJson('/backup/config_change/?uuid=' + encodeURIComponent(uuid)).then(function (response) {
      m.label.textContent = 'Configuration change of ' + response.router;
      m.action.textContent = 'Open the full comparison';
      m.action.href = response.compare_url;
      m.action.target = '_self';
      m.action.classList.remove('d-none');
      withDiff2Html(function () {
        var header = paragraph('Backup ' + response.previous.id + ' of ' + response.previous.date +
                               ' → backup ' + response.backup.id + ' of ' + response.backup.date);
        var holder = document.createElement('div');
        // diff2html escapes the configuration lines it renders
        holder.innerHTML = Diff2Html.html(response.diff, {inputFormat: 'diff', showFiles: false, matching: 'lines',
                                                          outputFormat: 'side-by-side'});
        m.body.replaceChildren(header, holder);
      });
    }).catch(function (error) {
      m.label.textContent = 'Configuration change';
      m.body.replaceChildren(paragraph(error.message));
    });
  }

  function auditValue(value) {
    if (value === true) { return 'yes'; }
    if (value === false) { return 'no'; }
    if (value === null || value === undefined) { return ''; }
    if (Array.isArray(value)) { return value.join(', '); }
    return String(value);
  }
  function auditActor(r) {
    if (!r.actor) { return r.actor_kind ? 'by ' + r.actor_kind : ''; }
    if (r.level === null || r.level === undefined) { return 'by ' + r.actor; }
    return 'by ' + r.actor + ' (level ' + r.level + ')';
  }
  function auditAddress(r) {
    if (!r.ip) { return ''; }
    var text = 'from ' + r.ip;
    if (r.forwarded && r.forwarded !== r.ip) { text += ' (' + r.forwarded + ')'; }
    if (r.user_agent) { text += ' — ' + r.user_agent; }
    return text;
  }

  // What an entry of the change log was, field by field. Each row links to it
  // with class="audit-change-link" and the uuid of the entry in data-uuid.
  function showAuditChange(uuid) {
    var m = openModal('Change');
    getJson('/audit_log/change_detail/?uuid=' + encodeURIComponent(uuid)).then(function (r) {
      m.label.textContent = r.action + ' ' + r.model;
      var parts = [];
      var headline = [];
      if (r.object) { headline.push(r.object); }
      var actor = auditActor(r);
      if (actor) { headline.push(actor); }
      headline.push(r.moment);
      parts.push(paragraph(headline.join(' · ')));
      var address = auditAddress(r);
      if (address) { parts.push(paragraph(address)); }
      if (!r.changes.length) {
        // A bulk entry counts rows instead of naming fields
        parts.push(paragraph(r.count ? r.count + ' rows were affected.' : 'Nothing was recorded for this entry.'));
        m.body.replaceChildren.apply(m.body, parts);
        return;
      }
      var table = document.createElement('table');
      table.className = 'table table-sm';
      var head = table.createTHead().insertRow();
      ['Field', 'Before', 'After'].forEach(function (title) {
        var th = document.createElement('th');
        th.textContent = title;
        head.appendChild(th);
      });
      var rows = table.createTBody();
      r.changes.forEach(function (change) {
        var row = rows.insertRow();
        // Text only, never markup: a router named <script> is a name
        [change.field, auditValue(change.old), auditValue(change.new)].forEach(function (value) {
          var cell = row.insertCell();
          cell.className = 'audit-change-value';
          cell.textContent = value;
        });
      });
      parts.push(table);
      m.body.replaceChildren.apply(m.body, parts);
    }).catch(function (error) {
      m.label.textContent = 'Change';
      m.body.replaceChildren(paragraph(error.message));
    });
  }

  // "Update available" in the footer: the operator's own changelog endpoint,
  // when there is one, in the popup - its notes as text, never as markup
  function showChangelog(event, link) {
    var url = link.dataset.changelogUrl;
    if (!url) { return; }   // no endpoint of the operator's own: the link opens the release
    event.preventDefault();
    var m = openModal('RouterFleet updates');
    var separator = url.indexOf('?') > -1 ? '&' : '?';
    getJson(url + separator + 'app=routerfleet&version=' + encodeURIComponent(link.dataset.version || ''))
      .then(function (response) {
        var updates = (response.updates || []).slice().sort(function (a, b) {
          return new Date(b.release_date) - new Date(a.release_date);
        });
        var parts = updates.map(function (update) {
          var box = document.createElement('div');
          var title = document.createElement('h5');
          title.textContent = 'Version ' + update.version;
          var notes = paragraph(String(update.release_notes || ''), false);
          notes.style.whiteSpace = 'pre-line';
          box.append(title, notes);
          return box;
        });
        m.body.replaceChildren.apply(m.body, parts.length ? parts : [paragraph('No release notes.')]);
        m.action.textContent = 'Update instructions';
        m.action.href = link.dataset.instructions || link.href;
        m.action.target = '_blank';
        m.action.rel = 'noopener';
        m.action.classList.remove('d-none');
      }).catch(function () {
        m.label.textContent = 'Error';
        m.body.replaceChildren(paragraph('The update information could not be fetched. Please try again later.'));
      });
  }

  /* --- Small behaviours, declared in the markup -------------------------
     No inline handlers (the Content-Security-Policy forbids them):
     data-rf-toggle="<selector>"  shows or hides what the selector names
     data-rf-alert="<text>"       says something, as text
     data-rf-differs-from="<id>"  a select that may not hold the other's value */
  document.addEventListener('click', function (event) {
    var control = event.target.closest('[data-rf-toggle], [data-rf-alert]');
    if (!control) { return; }
    event.preventDefault();
    if (control.dataset.rfToggle) {
      document.querySelectorAll(control.dataset.rfToggle).forEach(function (target) {
        target.classList.toggle('d-none');
        // An inline confirmation that opens: its answer gets the focus
        var answer = !target.classList.contains('d-none') && target.querySelector('input:not([type=hidden])');
        if (answer) { answer.focus(); }
      });
    } else {
      window.rfNoteInline(control, control.dataset.rfAlert);
    }
  });
  document.addEventListener('change', function (event) {
    var select = event.target;
    if (!select.matches || !select.matches('select[data-rf-differs-from]')) { return; }
    var other = document.getElementById(select.dataset.rfDiffersFrom);
    if (select.value && other && other.value === select.value) {
      window.rfNoteInline(select, select.dataset.rfDiffersMessage || 'The two choices must differ.');
      if (select.tomselect) { select.tomselect.clear(true); } else { select.value = ''; }
    }
  });

  document.addEventListener('click', function (event) {
    var link = event.target.closest('.config-change-link, .audit-change-link, #btn_update_changelog');
    if (!link) { return; }
    if (link.matches('.config-change-link')) { event.preventDefault(); showConfigChange(link.dataset.uuid); }
    else if (link.matches('.audit-change-link')) { event.preventDefault(); showAuditChange(link.dataset.uuid); }
    else { showChangelog(event, link); }
  });

  /* --- Actions, declared in the markup ---------------------------------
     A button or link carries what it does; no inline handler, and values come
     from attributes Django escapes - a device name can not break out of a
     JavaScript string any more.
       data-rf-confirm="<question>"        asked first, inline (Confirm / Cancel)
       data-rf-confirm-sum="<question>"    asked inline with a small sum;
       data-rf-confirm-label="<text>"        the confirm button (default: the word)
       data-rf-confirm-word="<word>"         the word sent as "confirmation"
       data-rf-post="<url>"                a POST to the url (empty: this page)
       data-rf-field-<name>="<value>"        with these fields
       data-rf-submit="<form id>"          or: submits that form, the word put
                                             in its "confirmation" field
       data-rf-call="<name>"               or: a function of the page's own
                                             script, registered in rfActions */
  window.rfActions = window.rfActions || {};
  // The Delete link of a form: the page declares what deleting is (a hidden
  // #rf-delete-action carrying the attributes above)
  // The confirmation shows beside the link that was clicked, not the hidden button
  var visibleTrigger = null;
  window.rfActions.openCommandDialog = function (control) {
    var declared = document.getElementById('rf-delete-action');
    if (declared) { visibleTrigger = control; declared.click(); }
  };
  function fieldsOf(element) {
    var fields = {};
    Object.keys(element.dataset).forEach(function (key) {
      if (key.indexOf('rfField') === 0 && key.length > 7) {
        var name = key.slice(7);
        fields[name.charAt(0).toLowerCase() + name.slice(1).replace(/[A-Z]/g, function (c) { return '_' + c.toLowerCase(); })] = element.dataset[key];
      }
    });
    return fields;
  }
  document.addEventListener('click', function (event) {
    var control = event.target.closest('[data-rf-post], [data-rf-submit], [data-rf-call]');
    if (!control || control.disabled) { return; }
    event.preventDefault();
    var data = control.dataset;
    var anchor = control;
    if (control.offsetParent === null && visibleTrigger) { anchor = visibleTrigger; }
    visibleTrigger = null;
    if (data.rfConfirmSum || data.rfConfirm) {
      window.rfConfirmInline(anchor, data.rfConfirmSum || data.rfConfirm, data.rfConfirmWord || 'yes',
                             Boolean(data.rfConfirmSum), data.rfConfirmLabel || (data.rfConfirmSum ? '' : 'Confirm'))
        .then(function (word) { if (word) { perform(control, data, data.rfConfirmSum ? word : null, event); } });
      return;
    }
    perform(control, data, null, event);
  });
  function perform(control, data, word, event) {
    if (data.rfCall) {
      var action = window.rfActions[data.rfCall];
      if (typeof action === 'function') { action(control, event); }
      return;
    }
    if (data.rfSubmit !== undefined) {
      var form = document.getElementById(data.rfSubmit);
      if (!form) { return; }
      if (word && form.elements.confirmation) { form.elements.confirmation.value = word; }
      form.submit();
      return;
    }
    var fields = fieldsOf(control);
    if (word) { fields.confirmation = word; }
    window.postAction(data.rfPost || (window.location.pathname + window.location.search), fields);
  }
  // A form sent in the background (data-rf-inline-submit="<row selector>"):
  // its row goes at once, without reloading the page; a failure reloads it
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.matches || !form.matches('form[data-rf-inline-submit]')) { return; }
    event.preventDefault();
    var row = form.closest(form.dataset.rfInlineSubmit) || form;
    row.classList.add('rf-row-leaving');
    // getAttribute: a field named "action" hides form.action
    fetch(form.getAttribute('action') || window.location.href, {method: 'POST', body: new FormData(form), credentials: 'same-origin',
                                                headers: {'Accept': 'application/json'}})
      .then(function (response) {
        if (!response.ok) { throw new Error(response.status); }
        setTimeout(function () { row.remove(); }, 200);
      })
      .catch(function () { window.location.reload(); });
  });
  // A list of checkboxes with a limit (data-rf-pick-limit="<n>"): the count of
  // chosen ones shown, the others greyed out once the limit is reached
  function updatePick(box) {
    var limit = Number(box.dataset.rfPickLimit) || 0;
    var boxes = box.querySelectorAll('input[type=checkbox]');
    var chosen = box.querySelectorAll('input[type=checkbox]:checked').length;
    boxes.forEach(function (input) {
      if (input.dataset.rfLocked === undefined) { input.dataset.rfLocked = input.disabled ? '1' : '0'; }
      if (input.dataset.rfLocked === '1') { return; }
      input.disabled = !input.checked && limit > 0 && chosen >= limit;
    });
    var count = box.querySelector('[data-rf-pick-count]');
    if (count) { count.textContent = chosen + ' of ' + limit + ' chosen'; }
  }
  document.addEventListener('change', function (event) {
    var box = event.target.closest && event.target.closest('[data-rf-pick-limit]');
    if (box) { updatePick(box); }
  });
  document.querySelectorAll('[data-rf-pick-limit]').forEach(updatePick);
  // A choice that saves itself: the form is sent when it changes
  document.addEventListener('change', function (event) {
    var input = event.target;
    if (input.matches && input.matches('[data-rf-autosubmit]') && input.form) { input.form.submit(); }
  });

  /* --- Test a device's connection ---------------------------------------
     #test-connection     the device form: the values in the form, saves nothing
     #test-connection-stored  router details: the saved settings (redrawn by
                          live updates, so both are found by delegation) */
  document.addEventListener('click', function (event) {
    var button = event.target.closest('#test-connection, #test-connection-stored');
    if (!button) { return; }
    event.preventDefault();
    var stored = button.id === 'test-connection-stored';
    var result = document.getElementById(stored ? 'test-connection-stored-result' : 'test-connection-result');
    var base = stored ? 'small' : 'd-block mt-2 small';
    var data = stored ? new FormData() : new FormData(button.form);
    if (button.dataset.uuid) { data.set('uuid', button.dataset.uuid); }
    if (stored) { data.append('stored', '1'); }
    data.set('csrfmiddlewaretoken', csrfToken());
    if (!stored) { button.disabled = true; }
    result.className = base + ' text-muted';
    result.textContent = 'Connecting...';
    fetch('/router/test_connection/', {method: 'POST', body: data, credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (answer) {
        result.className = base + ' ' + (answer.success ? 'text-success' : 'text-danger');
        result.textContent = answer.message;
      })
      .catch(function () { result.className = base + ' text-danger'; result.textContent = 'The test could not be run.'; })
      .then(function () { button.disabled = false; });
  });

  /* --- Parts shown by a choice, and a filter over rows -------------------
     data-rf-show-when="<radio name>" data-for="<values>"  shown while the chosen
                                                           radio has one of them
     data-rf-show-when="#<checkbox id>"                    shown while ticked
     data-rf-filter="<selector>"   on a text field: shows the elements whose
                                   text contains what is typed */
  function refreshShown() {
    document.querySelectorAll('[data-rf-show-when]').forEach(function (element) {
      var when = element.dataset.rfShowWhen;
      var show;
      if (when.charAt(0) === '#') {
        var box = document.querySelector(when);
        show = !box || box.checked;
      } else {
        var chosen = document.querySelector('input[name="' + CSS.escape(when) + '"]:checked');
        show = (element.dataset.for || '').split(' ').indexOf(chosen ? chosen.value : '') > -1;
      }
      element.hidden = !show;
    });
  }
  window.rfRefreshShown = refreshShown;
  document.addEventListener('change', function (event) {
    if (event.target.matches && event.target.matches('input[type=radio], input[type=checkbox]')) { refreshShown(); }
  });
  document.addEventListener('input', function (event) {
    var field = event.target;
    if (!field.matches || !field.matches('[data-rf-filter]')) { return; }
    var words = field.value.toLowerCase();
    document.querySelectorAll(field.dataset.rfFilter).forEach(function (row) {
      row.hidden = row.textContent.toLowerCase().indexOf(words) === -1;
    });
  });

  /* --- The sidebar ----------------------------------------------------- */
  // Collapsed or open is remembered (a cookie the server reads, so the page
  // is drawn as it was left). On a touch screen the menu does not expand on
  // hover: iOS spends the first tap on a hover that shows the labels.
  function initSidebar() {
    if (window.matchMedia && window.matchMedia('(hover: none)').matches) {
      document.body.classList.add('sidebar-no-expand');
    }
    document.querySelectorAll('[data-lte-toggle="sidebar"]').forEach(function (toggle) {
      toggle.addEventListener('click', function () {
        setTimeout(function () {
          window.setCookie('sidebarState', document.body.classList.contains('sidebar-collapse') ? 'collapsed' : 'opened', 7);
        }, 400);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSidebar();
    refreshShown();
    window.rfInitSelects(document);
    window.rfInitTables(document);
    window.rfInitPanels(document);
  });
})();

/* Collapsible sections (details[data-rf-remember]): closed at first; what
   the user opens is remembered per page for the session. */
(function () {
  'use strict';
  function key(section) { return 'rf-section:' + window.location.pathname + ':' + section.dataset.rfRemember; }
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('details[data-rf-remember]').forEach(function (section) {
      try { if (sessionStorage.getItem(key(section)) === 'open') { section.open = true; } } catch (e) {}
      section.addEventListener('toggle', function () {
        try { sessionStorage.setItem(key(section), section.open ? 'open' : 'closed'); } catch (e) {}
      });
    });
  });
})();
