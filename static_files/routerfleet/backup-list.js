/* The backup list: the devices on the left are filtered as you type; ticking
   two backups compares them, the older against the newer. Delegated, so it
   keeps working when the live refresh swaps the page in. */
(function () {
  'use strict';
  function picks() { return Array.prototype.slice.call(document.querySelectorAll('#backup-table .compare-pick:checked')); }
  function refresh() {
    var button = document.getElementById('compare-two');
    if (!button) { return; }
    var count = picks().length;
    button.disabled = count !== 2;
    button.textContent = count === 2 ? 'Compare these two' : 'Compare two';
  }
  document.addEventListener('change', function (event) {
    if (event.target.classList && event.target.classList.contains('compare-pick')) {
      // Two at most: a third replaces the oldest pick
      var chosen = picks();
      if (chosen.length > 2) { chosen.filter(function (box) { return box !== event.target; })[0].checked = false; }
      refresh();
    }
  });
  document.addEventListener('click', function (event) {
    if (!event.target.closest || !event.target.closest('#compare-two')) { return; }
    var chosen = picks();
    if (chosen.length !== 2) { return; }
    chosen.sort(function (a, b) { return Number(a.dataset.created) - Number(b.dataset.created); });
    window.location.href = '/backup/compare/?uuid=' + encodeURIComponent(chosen[0].value) +
      '&compare_uuid=' + encodeURIComponent(chosen[1].value);
  });
  document.addEventListener('input', function (event) {
    if (event.target.id !== 'backup-device-search') { return; }
    var term = event.target.value.trim().toLowerCase();
    document.querySelectorAll('#backup-devices .rf-li').forEach(function (item) {
      item.hidden = term !== '' && (item.dataset.search || '').toLowerCase().indexOf(term) === -1;
    });
  });
})();
