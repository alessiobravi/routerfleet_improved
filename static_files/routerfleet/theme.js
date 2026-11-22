/* Light and dark mode. The choice is the browser's own (localStorage
   'rf-theme': auto, light or dark); "auto" follows the device. The page is
   given its theme before it is drawn (an inline script in base.html); this
   file switches it: the moon/sun in the top bar, and the Appearance choice in
   My Settings. */
(function () {
  function read() { try { return localStorage.getItem('rf-theme') || 'auto'; } catch (e) { return 'auto'; } }
  function systemDark() { return !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches); }

  function apply(choice) {
    var dark = choice === 'dark' || (choice === 'auto' && systemDark());
    document.documentElement.classList.toggle('rf-dark', dark);
    document.documentElement.dataset.theme = choice;
    if (document.body) { document.body.classList.toggle('dark-mode', dark); }
    var toggle = document.getElementById('theme-toggle');
    if (toggle) {
      toggle.querySelector('i').className = 'fas ' + (dark ? 'fa-sun' : 'fa-moon');
      toggle.title = dark ? 'Light mode' : 'Dark mode';
    }
    document.querySelectorAll('input[name="rf-theme"]').forEach(function (radio) { radio.checked = radio.value === choice; });
    window.dispatchEvent(new Event('rf-theme-change'));
  }

  window.rfSetTheme = function (choice) {
    try { localStorage.setItem('rf-theme', choice); } catch (e) {}
    apply(choice);
  };

  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function (event) {
      event.preventDefault();
      window.rfSetTheme(document.documentElement.classList.contains('rf-dark') ? 'light' : 'dark');
    });
  }
  document.querySelectorAll('input[name="rf-theme"]').forEach(function (radio) {
    radio.addEventListener('change', function () { if (radio.checked) { window.rfSetTheme(radio.value); } });
  });
  // "Automatic" follows the device when it changes - at sunset, for example
  if (window.matchMedia) {
    var query = matchMedia('(prefers-color-scheme: dark)');
    (query.addEventListener ? query.addEventListener.bind(query, 'change') : query.addListener.bind(query))(function () {
      if (read() === 'auto') { apply('auto'); }
    });
  }
  apply(read());
})();
