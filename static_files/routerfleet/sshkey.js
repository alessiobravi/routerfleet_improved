/* SSH key page: an administrator reveals the stored private key in its own
   box after a small sum the server checks; the key can then be edited. Saving
   a replaced key (or a passphrase change) asks for another sum - the one the
   reveal answer carried. Cancel puts the box back as it was. */
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('sshkey-form');
    if (!form) { return; }
    var box = document.getElementById('secret-box');
    var key = document.getElementById('id_private_key');
    var passphrase = document.getElementById('id_passphrase');
    var remove = document.getElementById('id_remove_passphrase');
    var revealUrl = form.dataset.revealUrl;
    var original = null;          // the revealed key, to tell whether it changed
    var saveSum = null;

    function csrf() { return form.querySelector('input[name=csrfmiddlewaretoken]').value; }
    function show(id, on) { var el = document.getElementById(id); if (el) { el.classList.toggle('d-none', !on); } }

    var revealButton = document.getElementById('reveal-button');
    if (revealButton) {
      revealButton.addEventListener('click', function () {
        show('reveal-confirm', true);
        document.getElementById('reveal-answer').focus();
      });
      document.getElementById('reveal-cancel').addEventListener('click', function () {
        show('reveal-confirm', false);
        document.getElementById('reveal-answer').value = '';
      });
      function reveal() {
        var data = new FormData();
        data.append('answer', document.getElementById('reveal-answer').value);
        data.append('csrfmiddlewaretoken', csrf());
        fetch(revealUrl, {method: 'POST', body: data, credentials: 'same-origin'})
          .then(function (r) { return r.json(); })
          .then(function (answer) {
            var error = document.getElementById('reveal-error');
            document.getElementById('reveal-answer').value = '';
            if (answer.private_key === undefined) {
              error.textContent = answer.error || 'Not revealed.';
              if (answer.first) {
                document.getElementById('reveal-first').textContent = answer.first;
                document.getElementById('reveal-second').textContent = answer.second;
              }
              return;
            }
            error.textContent = '';
            original = answer.private_key;
            saveSum = [answer.save_first, answer.save_second];
            key.value = original;
            key.readOnly = false;
            box.classList.remove('rf-secret-locked');
            show('reveal-confirm', false);
            // The button becomes Hide / Protect: back to the stored key, hidden
            var cancel = revealButton.cloneNode(false);
            cancel.innerHTML = '<i class="fas fa-eye-slash me-1"></i>Hide / Protect';
            cancel.classList.replace('btn-outline-danger', 'btn-outline-secondary');
            cancel.addEventListener('click', function () { window.location.reload(); });
            revealButton.replaceWith(cancel);
          })
          .catch(function () { document.getElementById('reveal-error').textContent = 'The key could not be revealed.'; });
      }
      document.getElementById('reveal-ok').addEventListener('click', reveal);
      document.getElementById('reveal-answer').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); reveal(); }
      });
    }

    function changesSecret() {
      var keyChanged = original === null ? key && !key.readOnly && key.value.trim() !== '' && revealUrl
                                         : key.value.trim() !== original.trim();
      return Boolean(keyChanged || (passphrase && passphrase.value) || (remove && remove.checked));
    }

    form.addEventListener('submit', function (event) {
      var changing = revealUrl && changesSecret();
      // An unchanged revealed key is not sent: empty keeps the stored one
      if (original !== null && key.value.trim() === original.trim()) { key.value = ''; }
      if (!changing || document.getElementById('confirm-answer').value) { return; }
      event.preventDefault();
      if (!saveSum) {
        window.rfNoteInline(event.submitter || document.getElementById('reveal-button') || form,
                            'Reveal the private key first: replacing it, or changing its passphrase, starts there.');
        return;
      }
      document.getElementById('save-first').textContent = saveSum[0];
      document.getElementById('save-second').textContent = saveSum[1];
      show('save-confirm', true);
      document.getElementById('save-answer').focus();
    });
    var saveOk = document.getElementById('save-ok');
    if (saveOk) {
      function confirmSave() {
        document.getElementById('confirm-answer').value = document.getElementById('save-answer').value || '0';
        form.requestSubmit ? form.requestSubmit() : form.submit();
      }
      saveOk.addEventListener('click', confirmSave);
      document.getElementById('save-answer').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); confirmSave(); }
      });
      document.getElementById('save-cancel').addEventListener('click', function () {
        show('save-confirm', false);
        document.getElementById('save-answer').value = '';
      });
    }
  });
})();
