/* One search for everything: the header's magnifying glass, Ctrl+K (Cmd+K)
   or / anywhere outside a text field. Asks /search/ as you type and lists
   what it finds by kind; arrows move, Enter opens, Esc closes. */
(function () {
  'use strict';
  var dialog = document.getElementById('rf-search');
  var input = document.getElementById('rf-search-input');
  var list = document.getElementById('rf-search-results');
  var opener = document.getElementById('rf-search-open');
  if (!dialog || !input || !list) { return; }
  var timer = null;
  var asked = '';
  var active = -1;

  function links() { return Array.prototype.slice.call(list.querySelectorAll('a.rf-search-item')); }

  function mark(index) {
    var items = links();
    if (!items.length) { active = -1; return; }
    active = (index + items.length) % items.length;
    items.forEach(function (item, i) {
      item.classList.toggle('on', i === active);
      item.setAttribute('aria-selected', i === active ? 'true' : 'false');
    });
    items[active].scrollIntoView({block: 'nearest'});
  }

  function note(text) {
    list.textContent = '';
    var line = document.createElement('div');
    line.className = 'rf-search-note';
    line.textContent = text;
    list.appendChild(line);
    input.setAttribute('aria-expanded', 'false');
  }

  function show(groups) {
    list.textContent = '';
    active = -1;
    if (!groups.length) { note('Nothing found.'); return; }
    groups.forEach(function (group) {
      var title = document.createElement('div');
      title.className = 'rf-search-group';
      title.textContent = group.title;
      list.appendChild(title);
      group.items.forEach(function (item) {
        var link = document.createElement('a');
        link.className = 'rf-search-item';
        link.href = item.href;
        link.setAttribute('role', 'option');
        var icon = document.createElement('i');
        icon.className = 'fas ' + item.icon;
        icon.setAttribute('aria-hidden', 'true');
        var label = document.createElement('span');
        label.className = 'rf-search-label';
        label.textContent = item.label;
        var sub = document.createElement('span');
        sub.className = 'rf-search-sub';
        sub.textContent = item.sub || '';
        link.appendChild(icon);
        link.appendChild(label);
        link.appendChild(sub);
        list.appendChild(link);
      });
    });
    input.setAttribute('aria-expanded', 'true');
    mark(0);
  }

  function ask() {
    var query = input.value.trim();
    if (query === asked) { return; }
    asked = query;
    if (query.length < 2) { note('Type at least two letters.'); return; }
    fetch('/search/?q=' + encodeURIComponent(query), {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (response) {
        if (window.rfSessionGone && window.rfSessionGone(response)) { throw new Error('signed out'); }
        if (!response.ok) { throw new Error(response.status); }
        return response.json();
      })
      .then(function (data) { if (query === asked) { show(data.groups || []); } })
      .catch(function () { if (query === asked) { note('The search did not answer.'); } });
  }

  function open() {
    if (dialog.open) { return; }
    if (typeof dialog.showModal === 'function') { dialog.showModal(); } else { dialog.setAttribute('open', ''); }
    input.value = '';
    asked = '';
    note('Type at least two letters.');
    input.focus();
  }

  if (opener) {
    opener.addEventListener('click', function (event) { event.preventDefault(); open(); });
  }
  document.addEventListener('keydown', function (event) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;
    if ((event.key === 'k' || event.key === 'K') && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      open();
    } else if (event.key === '/' && !typing && !dialog.open) {
      event.preventDefault();
      open();
    }
  });
  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(ask, 150);
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowDown') { event.preventDefault(); mark(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); mark(active - 1); }
    else if (event.key === 'Enter') {
      var items = links();
      if (items[active]) { event.preventDefault(); window.location.assign(items[active].href); }
    }
  });
  // A click on the backdrop (outside the box) closes it
  dialog.addEventListener('click', function (event) { if (event.target === dialog) { dialog.close(); } });
})();
