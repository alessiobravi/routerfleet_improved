/* Confirming an action that can not be undone: a small sum instead of typing a
   word. rfConfirm(message, word) asks; on the right answer it returns `word` -
   the confirmation the server still checks - otherwise null. */
(function () {
  window.rfConfirm = function (message, word) {
    var a = 2 + Math.floor(Math.random() * 8);
    var b = 2 + Math.floor(Math.random() * 8);
    var answer = window.prompt(message + '\n\nTo confirm, how much is ' + a + ' + ' + b + '?');
    if (answer === null) { return null; }
    if (parseInt(String(answer).trim(), 10) === a + b) { return word; }
    window.alert('That is not ' + a + ' + ' + b + '. Nothing was done.');
    return null;
  };
})();
