/* My Settings: adding a passkey, where the browser can. A passkey is bound to
   a name, so not offered when the page is opened by IP address. */
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', function () {
    var onIp = /^[0-9.]+$|^\[/.test(window.location.hostname);
    if (!window.RouterFleetPasskeys || !RouterFleetPasskeys.supported() || onIp) { return; }
    document.getElementById('passkey-add').classList.remove('d-none');
    document.getElementById('passkey-unsupported').classList.add('d-none');
    document.getElementById('passkey-add-button').addEventListener('click', function () {
      var error = document.getElementById('passkey-error');
      error.textContent = '';
      RouterFleetPasskeys.register(document.getElementById('passkey-name').value).then(function (result) {
        window.location = result.redirect || '/user/settings/';
      }).catch(function (reason) {
        if (reason && reason.name !== 'NotAllowedError') {
          error.textContent = reason.message || 'The passkey could not be added.';
        }
      });
    });
  });
})();
