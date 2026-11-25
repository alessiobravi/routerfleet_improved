/* Confirming an action: inline, beside what was clicked - never a browser
   popup. rfConfirmInline(anchor, message, word, withSum) shows a small panel
   after the anchor's row or block: the question, and either a small sum to
   answer or a plain Confirm. It resolves with `word` - the confirmation the
   server still checks - or null when cancelled. One panel at a time; Enter
   confirms, Escape cancels; a wrong answer asks a new sum. */
(function () {
  'use strict';
  var open = null;   // {panel, resolve}

  function close(result) {
    if (!open) { return; }
    var current = open;
    open = null;
    current.panel.remove();
    if (current.anchor && current.anchor.focus) { current.anchor.focus({preventScroll: true}); }
    current.resolve(result);
  }

  function place(anchor, panel) {
    // Inside a table cell or a list item: in it; a settings row: after it;
    // otherwise after the block the control sits in
    var cell = anchor.closest('td, th, li');
    if (cell) { cell.appendChild(panel); return; }
    var row = anchor.closest('.rf-set-row');
    if (row) { row.after(panel); return; }
    var block = anchor.closest('.text-center, .rf-o-hero-actions, .btn-group, .d-flex, p, div') || anchor;
    block.after(panel);
  }

  function label(word) {
    var text = String(word || 'confirm').replace(/[0-9]+$/, '');
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /* A note beside what was clicked, instead of a browser alert: the text and
     an OK that dismisses it. */
  window.rfNoteInline = function (anchor, text) {
    close(null);
    var panel = document.createElement('div');
    panel.className = 'rf-confirm rf-confirm-inline rf-note-inline';
    panel.setAttribute('role', 'status');
    var message = document.createElement('div');
    message.className = 'mb-2';
    message.textContent = text;
    var ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'btn btn-sm btn-outline-secondary';
    ok.textContent = 'OK';
    panel.appendChild(message);
    panel.appendChild(ok);
    open = {panel: panel, resolve: function () {}, anchor: anchor};
    ok.addEventListener('click', function () { close(null); });
    panel.addEventListener('keydown', function (event) { if (event.key === 'Escape' || event.key === 'Enter') { event.preventDefault(); close(null); } });
    place(anchor, panel);
    ok.focus();
  };

  window.rfConfirmInline = function (anchor, message, word, withSum, confirmLabel) {
    close(null);
    return new Promise(function (resolve) {
      var a = 0, b = 0;
      var panel = document.createElement('div');
      panel.className = 'rf-confirm rf-confirm-inline';
      panel.setAttribute('role', 'group');
      panel.setAttribute('aria-label', message);
      var question = document.createElement('div');
      question.className = 'rf-confirm-question';
      question.textContent = message;
      panel.appendChild(question);
      var input = null, sumLabel = null;
      var group = document.createElement('div');
      group.className = withSum ? 'input-group input-group-sm' : 'd-flex gap-2';
      if (withSum) {
        var id = 'rf-confirm-' + Date.now();
        sumLabel = document.createElement('label');
        sumLabel.className = 'small mb-1 d-block';
        sumLabel.htmlFor = id;
        panel.appendChild(sumLabel);
        input = document.createElement('input');
        input.type = 'text';
        input.id = id;
        input.className = 'form-control';
        input.inputMode = 'numeric';
        input.autocomplete = 'off';
        group.appendChild(input);
      }
      var ok = document.createElement('button');
      ok.type = 'button';
      ok.className = 'btn btn-danger' + (withSum ? '' : ' btn-sm');
      ok.textContent = confirmLabel || label(word);
      var cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn btn-outline-secondary' + (withSum ? '' : ' btn-sm');
      cancel.textContent = 'Cancel';
      group.appendChild(ok);
      group.appendChild(cancel);
      panel.appendChild(group);
      var error = document.createElement('div');
      error.className = 'text-danger small mt-1';
      error.setAttribute('role', 'alert');
      panel.appendChild(error);

      function newSum() {
        a = 2 + Math.floor(Math.random() * 8);
        b = 2 + Math.floor(Math.random() * 30);
        sumLabel.textContent = 'To confirm: how much is ' + a + ' + ' + b + '?';
      }
      function confirm() {
        if (!withSum) { close(word); return; }
        if (parseInt(String(input.value).trim(), 10) === a + b) { close(word); return; }
        error.textContent = 'That is not ' + a + ' + ' + b + '. Try this one.';
        input.value = '';
        newSum();
        input.focus();
      }
      if (withSum) { newSum(); }
      ok.addEventListener('click', confirm);
      cancel.addEventListener('click', function () { close(null); });
      panel.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); confirm(); }
        if (event.key === 'Escape') { event.preventDefault(); close(null); }
      });
      open = {panel: panel, resolve: resolve, anchor: anchor};
      place(anchor, panel);
      (input || ok).focus();
      panel.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    });
  };
})();
