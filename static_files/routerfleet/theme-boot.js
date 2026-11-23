/* Light or dark, before anything is drawn - loaded in <head>, before the
   stylesheets apply, so there is no white flash at night. The choice is the
   browser's own (localStorage 'rf-theme': auto, light or dark); "auto" follows
   the device. Bootstrap 5.3 reads data-bs-theme; RouterFleet's own components
   read the rf-dark class. A static file, so the Content-Security-Policy needs
   no inline script for it. */
(function () {
  var choice = 'auto';
  try { choice = localStorage.getItem('rf-theme') || 'auto'; } catch (e) {}
  var dark = choice === 'dark' || (choice === 'auto' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  var root = document.documentElement;
  root.setAttribute('data-bs-theme', dark ? 'dark' : 'light');
  root.classList.toggle('rf-dark', dark);
  root.dataset.theme = choice;
})();
