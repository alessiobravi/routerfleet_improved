/* Notifications: a balloon at the top centre of the page, gone after 5 seconds
   or on a click. Text only - nothing passed in is read as HTML, so a device
   name can not inject markup. A newline in the body starts a new line.

   rfNotify('success' | 'info' | 'warning' | 'danger' | 'error', title, body) */
(function () {
  // The colours are in app.css (.rf-kind-...): no style is written here, so
  // the Content-Security-Policy needs no inline style
  var KINDS = {
    success: {icon: 'fa-check-circle'},
    info: {icon: 'fa-info-circle'},
    warning: {icon: 'fa-exclamation-triangle'},
    danger: {icon: 'fa-times-circle'}
  };

  function stack() {
    var element = document.getElementById('rf-notify');
    if (!element) {
      element = document.createElement('div');
      element.id = 'rf-notify';
      document.body.appendChild(element);
    }
    return element;
  }

  window.rfNotify = function (kind, title, body) {
    kind = kind === 'error' ? 'danger' : (KINDS[kind] ? kind : 'info');
    var balloon = document.createElement('div');
    balloon.className = 'rf-balloon rf-kind-' + kind;
    balloon.setAttribute('role', kind === 'danger' || kind === 'warning' ? 'alert' : 'status');
    balloon.title = 'Click to close';

    var icon = document.createElement('i');
    icon.className = 'fas ' + KINDS[kind].icon + ' rf-icon';
    icon.setAttribute('aria-hidden', 'true');
    var text = document.createElement('div');
    text.className = 'rf-text';
    if (title) {
      var heading = document.createElement('div');
      heading.className = 'rf-title';
      heading.textContent = title;
      text.appendChild(heading);
    }
    if (body) {
      var paragraph = document.createElement('div');
      paragraph.className = 'rf-body';
      paragraph.textContent = body;
      text.appendChild(paragraph);
    }
    var close = document.createElement('i');
    close.className = 'fas fa-times rf-close';
    close.setAttribute('aria-hidden', 'true');
    var timer = document.createElement('div');
    timer.className = 'rf-timer';
    balloon.append(icon, text, close, timer);

    var gone = false;
    function dismiss() {
      if (gone) { return; }
      gone = true;
      balloon.classList.add('rf-out');
      setTimeout(function () { balloon.remove(); }, 200);
    }
    balloon.addEventListener('click', dismiss);
    // The bar shows the time left; it pauses while the pointer rests on it
    timer.addEventListener('animationend', dismiss);
    stack().appendChild(balloon);
    return balloon;
  };

  // A message written "Title|Body" by the server
  window.rfNotifyMessage = function (kind, message) {
    var cut = message.indexOf('|');
    if (cut > -1) { return rfNotify(kind, message.slice(0, cut), message.slice(cut + 1)); }
    return rfNotify(kind, '', message);
  };
})();

// The messages the server queued for this page, and the errors of a form that
// was not saved, come as JSON data (template_messages.html) - not as scripts
document.addEventListener('DOMContentLoaded', function () {
  var data = document.getElementById('rf-page-messages');
  if (!data) { return; }
  var queued = JSON.parse(data.textContent);
  (queued.messages || []).forEach(function (m) { window.rfNotifyMessage(m[0] || 'info', m[1]); });
  (queued.errors || []).forEach(function (error) { window.rfNotify('danger', 'Not saved', error); });
});
