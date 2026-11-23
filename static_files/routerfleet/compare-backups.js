/* Comparing two backups: Changes (one column, wrapped), or diff2html side by
   side or line by line. The diff is read as data, never written into a script. */
(function () {
  'use strict';
  // Read as data, not written into the script: a configuration line can hold
  // anything, a backtick or a closing script tag included
  var diffString = JSON.parse(document.getElementById('diff-data').textContent);
  var lines = diffString.split('\n').filter(function (line) {
      return !line.startsWith('--- ') && !line.startsWith('+++ ');
  });
  var target = document.getElementById('diff-container');

  function el(tag, cls, text) {
      var node = document.createElement(tag);
      if (cls) { node.className = cls; }
      if (text !== undefined) { node.textContent = text; }   // text only, never markup
      return node;
  }

  // One column, wrapped: every changed part with its line numbers
  function renderChanges() {
      target.textContent = '';
      var added = 0, removed = 0, hunk = null, body = null, oldNo = 0, newNo = 0;
      lines.forEach(function (line) {
          var head = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
          if (head) {
              oldNo = +head[1]; newNo = +head[3];
              hunk = el('div', 'rf-chg-hunk');
              var last = newNo + Math.max((+(head[4] || 1)) - 1, 0);
              hunk.appendChild(el('div', 'rf-chg-head', 'Lines ' + newNo + (last > newNo ? '-' + last : '')));
              body = el('div');
              hunk.appendChild(body);
              target.appendChild(hunk);
              return;
          }
          if (!body || line === '\\ No newline at end of file') { return; }
          var kind = line[0] === '-' ? 'del' : line[0] === '+' ? 'add' : 'ctx';
          var row = el('div', 'rf-chg-line ' + kind);
          row.appendChild(el('span', 'rf-chg-no', kind === 'add' ? '' : String(oldNo)));
          row.appendChild(el('span', 'rf-chg-no', kind === 'del' ? '' : String(newNo)));
          row.appendChild(el('span', 'rf-chg-sign', kind === 'del' ? '−' : kind === 'add' ? '+' : ''));
          row.appendChild(el('span', 'rf-chg-text', line.slice(1) || ' '));
          body.appendChild(row);
          if (kind !== 'add') { oldNo++; }
          if (kind !== 'del') { newNo++; }
          if (kind === 'add') { added++; } else if (kind === 'del') { removed++; }
      });
      var sum = el('div', 'rf-chg-sum');
      if (!added && !removed) {
          target.appendChild(el('div', 'rf-chg-empty', 'The two backups have the same configuration.'));
      }
      sum.appendChild(el('span', 'add', '+' + added));
      sum.appendChild(document.createTextNode(' added, '));
      sum.appendChild(el('span', 'del', '−' + removed));
      sum.appendChild(document.createTextNode(' removed - lines with − are in backup ' + target.dataset.older + ', lines with + in backup ' + target.dataset.newer));
      target.insertBefore(sum, target.firstChild);
  }

  function renderDiff(viewType) {
      // diff2html needs the --- / +++ header to see a file at all
      target.innerHTML = Diff2Html.html(diffString, {
          inputFormat: 'diff', showFiles: false, matching: 'lines', outputFormat: viewType});
  }

  function show(mode) {
      if (mode === 'changes') { renderChanges(); } else { renderDiff(mode); }
      document.querySelectorAll('.rf-diff-modes .btn').forEach(function (b) {
          b.classList.toggle('active', b.dataset.mode === mode);
      });
      try { sessionStorage.setItem('rf-diff-mode', mode); } catch (e) {}
  }
  document.querySelectorAll('.rf-diff-modes .btn').forEach(function (b) {
      b.addEventListener('click', function () { show(b.dataset.mode); });
  });
  // A phone opens on Changes; a wider screen on side by side, unless
  // another view was chosen in this tab
  var chosen = null;
  try { chosen = sessionStorage.getItem('rf-diff-mode'); } catch (e) {}
  show(chosen || (window.matchMedia('(max-width: 767.98px)').matches ? 'changes' : 'side-by-side'));

})();
