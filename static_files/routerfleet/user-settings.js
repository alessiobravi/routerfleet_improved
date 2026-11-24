/* My Settings: adding a passkey, where the browser can - the row stays greyed
   out elsewhere. A passkey is bound to a name, so not on an IP address. */
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', function () {
    var onIp = /^[0-9.]+$|^\[/.test(window.location.hostname);
    if (!window.RouterFleetPasskeys || !RouterFleetPasskeys.supported() || onIp) { return; }
    // Passkeys can work here: the greyed-out row comes alive
    var row = document.getElementById('passkey-add');
    row.classList.remove('rf-set-disabled');
    row.removeAttribute('aria-disabled');
    document.getElementById('passkey-unsupported').textContent =
      "Sign in with your device's fingerprint, face or PIN. Name the new passkey after the device it is on.";
    document.getElementById('passkey-name').disabled = false;
    document.getElementById('passkey-add-button').disabled = false;
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

