/* The device console in the browser: xterm.js over a WebSocket.

   The one-time ticket is in this page only (sent with no-store), and goes to
   the server as the first message - never in a URL. A password typed here is
   sent once in that message, then the field is emptied.

   The connection closes when the page is closed or left (pagehide), with the
   Disconnect button, and whenever the server ends the session: signed out,
   idle, the time limit, or the device closing it. */
(function () {
  var ticketElement = document.getElementById('console-ticket');
  var ticket = ticketElement ? JSON.parse(ticketElement.textContent) : null;
  if (ticketElement) { ticketElement.remove(); }

  var state = document.getElementById('console-state');
  var dot = document.getElementById('console-dot');
  var clock = document.getElementById('console-clock');
  var disconnectButton = document.getElementById('console-disconnect');
  var loginForm = document.getElementById('console-login');
  var screen = document.getElementById('console-screen');

  // A device can print a link (OSC 8): opened only after a confirmation, only
  // http(s), in a tab that can not reach back to this page
  var linkHandler = {
    activate: function (event, uri) {
      var url;
      try { url = new URL(uri); } catch (e) { return; }
      if ((url.protocol === 'http:' || url.protocol === 'https:') && window.confirm('Open this link printed by the device?\n\n' + url.href)) {
        var opened = window.open(url.href, '_blank', 'noopener,noreferrer');
        if (opened) { opened.opener = null; }
      }
    }
  };
  // The font follows the space: a phone held upright gets a smaller one, so a
  // line of device output still fits (about 60 columns, never under 8 px);
  // landscape and larger screens keep 14 px. Turning the phone switches.
  var FULL = 14, SMALLEST = 8, PORTRAIT_COLUMNS = 60;
  function fontSize() {
    var portrait = window.matchMedia('(orientation: portrait)').matches;
    var width = screen.clientWidth || window.innerWidth;
    if (!portrait || window.innerWidth >= 768) { return FULL; }
    // a monospace character is about 0.6 of the font size wide
    return Math.max(SMALLEST, Math.min(FULL, Math.floor(width / (PORTRAIT_COLUMNS * 0.6))));
  }
  var term = new Terminal({cursorBlink: true, convertEol: false, fontSize: fontSize(), scrollback: 5000, linkHandler: linkHandler,
                           fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                           theme: {background: '#0f1a28', foreground: '#d8e6e3', cursor: '#14a89a'}});
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(screen);
  fit.fit();

  var socket = null, started = null, timer = null, encoder = new TextEncoder();

  function setState(text, kind) {
    state.textContent = text;
    dot.className = 'rf-console-dot' + (kind ? ' ' + kind : '');
  }

  var CLOSE_REASONS = {
    1000: 'Disconnected.',
    1006: 'The connection was lost.',
    4401: 'Not signed in.',
    4403: 'Not allowed, or this console link was already used or expired. Open the console again.',
    4500: 'Could not connect to the device.'
  };

  function connect(password) {
    if (!ticket) { setState('This console link was already used. Open the console again.', 'off'); return; }
    var url = (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/console/ws/';
    socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    setState('Connecting...', 'wait');
    socket.onopen = function () {
      var first = {ticket: ticket, cols: term.cols, rows: term.rows};
      if (password !== undefined) { first.password = password; }
      socket.send(JSON.stringify(first));
      ticket = null;            // one use only
      password = first = null;  // not kept
      disconnectButton.disabled = false;
      term.focus();
    };
    socket.onmessage = function (event) {
      if (typeof event.data === 'string') {
        var message;
        try { message = JSON.parse(event.data); } catch (e) { return; }
        if (message.type === 'status') {
          var kind = {success: 'on', error: 'off', warning: 'wait'}[message.level] || 'wait';
          setState(message.text, kind);
          term.writeln('\r\n\x1b[2m' + message.text.replace(/[\x00-\x1f]/g, ' ') + '\x1b[0m');
          if (message.level === 'success') { started = Date.now(); tick(); timer = setInterval(tick, 1000); }
        }
        return;
      }
      term.write(new Uint8Array(event.data));
    };
    socket.onclose = function (event) {
      clearInterval(timer);
      disconnectButton.disabled = true;
      if (state.textContent.indexOf('Session ended') !== 0 && state.textContent.indexOf('Could not') !== 0) {
        setState(CLOSE_REASONS[event.code] || 'Disconnected.', 'off');
      } else {
        dot.className = 'rf-console-dot off';
      }
      term.options.disableStdin = true;
      socket = null;
      showEnded();
    };
  }

  // When the connection is over, a way back to the devices list over the screen
  var ended = document.getElementById('console-ended');
  function showEnded() {
    if (!ended) { return; }
    document.getElementById('console-ended-reason').textContent = state.textContent;
    ended.hidden = false;
    document.getElementById('console-ended-list').focus();
  }
  if (ended) {
    document.getElementById('console-ended-keep').addEventListener('click', function () { ended.hidden = true; });
  }

  function tick() {
    if (!started) { return; }
    var seconds = Math.floor((Date.now() - started) / 1000);
    clock.textContent = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
  }

  function send(text) {
    if (socket && socket.readyState === WebSocket.OPEN) { socket.send(encoder.encode(text)); }
  }

  term.onData(send);
  term.onResize(function (size) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({type: 'resize', cols: size.cols, rows: size.rows}));
    }
  });
  function refit() {
    var size = fontSize();
    if (term.options.fontSize !== size) { term.options.fontSize = size; }
    fit.fit();   // tells the device the new size through onResize
  }
  window.addEventListener('resize', refit);
  window.addEventListener('orientationchange', refit);

  // Closing or leaving the page ends the session at once
  function closeNow() { if (socket) { try { socket.close(1000); } catch (e) {} } }
  window.addEventListener('pagehide', closeNow);
  window.addEventListener('beforeunload', closeNow);
  disconnectButton.addEventListener('click', function () { closeNow(); setState('Disconnected.', 'off'); });

  var KEYS = {esc: '\x1b', tab: '\t', 'ctrl-c': '\x03', 'ctrl-d': '\x04',
              up: '\x1b[A', down: '\x1b[B', right: '\x1b[C', left: '\x1b[D'};
  document.querySelectorAll('.rf-console-keys button').forEach(function (button) {
    button.addEventListener('click', function () { send(KEYS[button.dataset.key]); term.focus(); });
  });

  if (loginForm) {
    setState('Enter the password to connect', '');
    loginForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var field = document.getElementById('console-password');
      var password = field.value;
      field.value = '';
      loginForm.remove();
      connect(password);
      password = null;
    });
  } else {
    connect();
  }
})();
