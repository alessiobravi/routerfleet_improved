/* Live pages: anything in progress updates itself.

   A page marks a part of itself <div id="..." data-rf-live="active"> while
   something in it is still running (a job, a task, a backup, an information
   read) and "done" once it has settled. While any part is active the page is
   fetched again every few seconds (data-rf-live-interval, 3 by default) and
   the marked parts are swapped for their new version. When none is active any
   more, it stops.

   It pauses while the tab is hidden, never replaces a part the user is typing
   in, backs off when the server does not answer, and stops for good on a
   redirect (an expired session) instead of polling the sign-in page. */
(function () {
  var timer = null, failures = 0, stopped = false, pill = null;

  function parts(root) { return Array.prototype.slice.call((root || document).querySelectorAll('[data-rf-live][id]')); }
  function activeParts() { return parts().filter(function (p) { return p.getAttribute('data-rf-live') === 'active'; }); }
  // What is running, as the page says (data-rf-live-reason); a part marked
  // data-rf-live-quiet - the status page's cards - refreshes without the pill
  function reason() {
    var loud = activeParts().filter(function (p) { return !p.hasAttribute('data-rf-live-quiet'); });
    if (!loud.length) { return null; }
    return loud.map(function (p) { return p.getAttribute('data-rf-live-reason'); }).filter(Boolean)[0] || 'updating';
  }

  function showPill(state) {
    if (!pill) {
      pill = document.createElement('div');
      pill.id = 'rf-live-pill';
      pill.setAttribute('role', 'status');
      var dot = document.createElement('span');
      dot.className = 'rf-dot';
      dot.setAttribute('aria-hidden', 'true');
      var label = document.createElement('span');
      label.className = 'rf-label';
      pill.append(dot, label);
      document.body.appendChild(pill);
    }
    if (!state) { pill.remove(); pill = null; return; }
    pill.classList.toggle('rf-paused', state !== 'live');
    pill.title = 'This page refreshes by itself while something on it is running, and stops when it is done';
    pill.querySelector('.rf-label').textContent =
      state === 'live' ? 'Live: ' + (reason() || 'updating') : state === 'retry' ? 'Live: reconnecting' : 'Live updates stopped - reload the page';
  }

  function interval() {
    var seconds = activeParts().map(function (p) { return Number(p.getAttribute('data-rf-live-interval')) || 3; });
    return Math.max(2, Math.min.apply(null, seconds));
  }

  function schedule() {
    clearTimeout(timer);
    if (stopped) { return; }
    if (!activeParts().length) { showPill(null); return; }
    if (reason() === null && !failures) { showPill(null); } else { showPill(failures ? 'retry' : 'live'); }
    var wait = failures ? Math.min(30, interval() * Math.pow(2, failures)) : interval();
    timer = setTimeout(refresh, wait * 1000);
  }

  function refresh() {
    if (document.hidden) { return; }   // resumed by visibilitychange
    fetch(window.location.href, {credentials: 'same-origin', headers: {'X-RF-Live': '1'}, cache: 'no-store'})
      .then(function (response) {
        if (response.redirected) { throw {stop: true}; }
        if (!response.ok) { throw new Error(response.status); }
        return response.text();
      })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, 'text/html');
        parts().forEach(function (old) {
          var next = fresh.getElementById(old.id);
          if (!next) { return; }
          var focused = document.activeElement;
          if (focused && focused !== document.body && old.contains(focused) && /INPUT|TEXTAREA|SELECT/.test(focused.tagName)) { return; }
          if (next.outerHTML === old.outerHTML) { return; }
          // What was typed in a filter stays, and filters the fresh rows too
          var typed = {};
          old.querySelectorAll('input[data-rf-filter]').forEach(function (input) { typed[input.dataset.rfFilter] = input.value; });
          var adopted = document.importNode(next, true);
          old.replaceWith(adopted);
          if (window.rfInitPanels) { window.rfInitPanels(adopted); }
          if (window.rfInitTables) { window.rfInitTables(adopted); }
          if (window.rfInitSelects) { window.rfInitSelects(adopted); }
          adopted.querySelectorAll('input[data-rf-filter]').forEach(function (input) {
            if (typed[input.dataset.rfFilter]) {
              input.value = typed[input.dataset.rfFilter];
              input.dispatchEvent(new Event('input', {bubbles: true}));
            }
          });
        });
        failures = 0;
        schedule();
      })
      .catch(function (error) {
        if (error && error.stop) { stopped = true; showPill('stopped'); return; }
        failures += 1;
        if (failures > 6) { stopped = true; showPill('stopped'); return; }
        schedule();
      });
  }

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && !stopped && activeParts().length) { clearTimeout(timer); refresh(); }
  });
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', schedule); } else { schedule(); }
})();
