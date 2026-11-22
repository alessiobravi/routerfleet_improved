/* Notifications: a balloon at the top centre of the page, gone after 5 seconds
   or on a click. Text only - nothing passed in is read as HTML, so a device
   name can not inject markup. A newline in the body starts a new line.

   rfNotify('success' | 'info' | 'warning' | 'danger' | 'error', title, body) */
(function () {
  var SECONDS = 5;
  var KINDS = {
    success: {icon: 'fa-check-circle', color: '#14a89a'},
    info: {icon: 'fa-info-circle', color: '#2f6fd1'},
    warning: {icon: 'fa-exclamation-triangle', color: '#e8a317'},
    danger: {icon: 'fa-times-circle', color: '#d64545'}
  };
  var style = document.createElement('style');
  style.textContent = [
    '#rf-notify{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:2000;',
    'display:flex;flex-direction:column;align-items:center;gap:10px;width:calc(100% - 32px);max-width:520px;pointer-events:none}',
    '.rf-balloon{pointer-events:auto;cursor:pointer;width:100%;display:flex;gap:12px;align-items:flex-start;position:relative;overflow:hidden;',
    'background:#fff;color:#1d2733;border-radius:14px;padding:12px 16px 14px;border-left:5px solid var(--rf-kind);',
    'box-shadow:0 12px 32px rgba(15,34,56,.22),0 2px 6px rgba(15,34,56,.12);animation:rf-balloon-in .25s ease-out both}',
    '.rf-balloon.rf-out{animation:rf-balloon-out .2s ease-in both}',
    '.rf-balloon .rf-icon{color:var(--rf-kind);font-size:1.25rem;line-height:1.4}',
    '.rf-balloon .rf-text{flex:1;min-width:0;overflow-wrap:anywhere}',
    '.rf-balloon .rf-title{font-weight:700;margin-bottom:1px}',
    '.rf-balloon .rf-body{font-size:.92rem;white-space:pre-line;color:#3b4652}',
    '.rf-balloon .rf-close{color:#8a96a3;font-size:1rem;line-height:1.4}',
    '.rf-balloon .rf-timer{position:absolute;left:0;bottom:0;height:3px;width:100%;background:var(--rf-kind);opacity:.55;',
    'transform-origin:left;animation:rf-timer ' + SECONDS + 's linear forwards}',
    '.rf-balloon:hover .rf-timer{animation-play-state:paused}',
    '@keyframes rf-balloon-in{from{opacity:0;transform:translateY(-12px) scale(.98)}to{opacity:1;transform:none}}',
    '@keyframes rf-balloon-out{to{opacity:0;transform:translateY(-8px)}}',
    '@keyframes rf-timer{from{transform:scaleX(1)}to{transform:scaleX(0)}}',
    '@media (prefers-reduced-motion:reduce){.rf-balloon,.rf-balloon.rf-out{animation:none}}'
  ].join('');

  function stack() {
    var element = document.getElementById('rf-notify');
    if (!element) {
      document.head.appendChild(style);
      element = document.createElement('div');
      element.id = 'rf-notify';
      document.body.appendChild(element);
    }
    return element;
  }

  window.rfNotify = function (kind, title, body) {
    kind = kind === 'error' ? 'danger' : (KINDS[kind] ? kind : 'info');
    var balloon = document.createElement('div');
    balloon.className = 'rf-balloon';
    balloon.style.setProperty('--rf-kind', KINDS[kind].color);
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
