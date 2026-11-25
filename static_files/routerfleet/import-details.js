/* An import: the rows still pending are run one after the other, each
   showing how it went - when the Start button on this page is clicked, never
   by merely opening the page (a link from elsewhere could do that). */
document.addEventListener('DOMContentLoaded', function () {
  'use strict';
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  function badge(cell, kind, text) {
    var span = document.createElement('span');
    span.className = 'badge text-bg-' + kind;
    span.textContent = text;
    cell.replaceChildren(span);
  }
  async function run(uuid) {
    var statusCell = document.getElementById('status-' + uuid);
    var messageCell = document.getElementById('errormessage-' + uuid);
    badge(statusCell, 'info', 'Working...');
    try {
      var response = await fetch('/router/import_tool/run_import_task/?uuid=' + encodeURIComponent(uuid),
                                 {method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'});
      var data = await response.json();
      if (data.status === 'success') {
        badge(statusCell, 'success', 'Success');
      } else if (data.status === 'error') {
        badge(statusCell, 'danger', 'Error');
        messageCell.textContent = data.error_message;
      }
    } catch (error) {
      badge(statusCell, 'danger', 'Error');
      messageCell.textContent = 'Request failed.';
    }
  }
  var start = document.getElementById('rf-start-import');
  if (!start) { return; }
  start.addEventListener('click', async function () {
    start.disabled = true;
    for (var row of document.querySelectorAll('table tbody tr')) {
      var uuidCell = row.querySelector('td:nth-child(2)');
      if (!uuidCell) { continue; }
      var uuid = uuidCell.textContent.trim();
      var statusCell = document.getElementById('status-' + uuid);
      var current = statusCell && statusCell.querySelector('.badge');
      if (current && current.textContent.trim() === 'Pending') { await run(uuid); }
    }
    start.textContent = 'Import finished';
  });
});
