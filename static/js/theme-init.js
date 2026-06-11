/* theme-init.js — applied as early as possible to prevent FOUC */
(function () {
  var t = localStorage.getItem('ss-theme');
  if (t === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
}());
