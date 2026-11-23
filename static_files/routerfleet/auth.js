/* The sign-in pages: a user name is typed in lower case without spaces, and
   a passkey signs in where the browser has one. A static file - the
   Content-Security-Policy allows no inline script. */
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', function () {
    var username = document.querySelector('input[name="username"]');
    if (username) {
      username.addEventListener('input', function () { this.value = this.value.toLowerCase().replace(/\s/g, ''); });
    }
    var area = document.getElementById('passkey-area');
    if (!area || !window.RouterFleetPasskeys || !RouterFleetPasskeys.supported()) { return; }
    area.classList.remove('d-none');
    document.getElementById('passkey-button').addEventListener('click', function () {
      var error = document.getElementById('passkey-error');
      error.textContent = '';
      RouterFleetPasskeys.signIn().then(function (result) {
        window.location = result.redirect || '/';
      }).catch(function (reason) {
        // Cancelling the browser's dialog is not an error worth showing
        if (reason && reason.name !== 'NotAllowedError') {
          error.textContent = reason.message || 'The passkey could not be used.';
        }
      });
    });
  });
})();
