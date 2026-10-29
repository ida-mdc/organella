/** A section's header questions stick under its pinned structure bar, so they need that bar's height. */
var _stickyWatched = false;

function syncStickyBars() {
  [['s-entities', 'entity-pills'], ['s-groups', 'ct-pills']].forEach(function(pair) {
    var section = document.getElementById(pair[0]), bar = document.getElementById(pair[1]);
    if (!section || !bar || !section.style || !section.style.setProperty) return;
    if (bar.offsetHeight) section.style.setProperty('--stick-h', bar.offsetHeight + 'px');
    // The bar wraps to more or fewer rows as the page narrows or widens, so its height is followed.
    if (!_stickyWatched && typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function() {
        section.style.setProperty('--stick-h', bar.offsetHeight + 'px');
      }).observe(bar);
    }
  });
  if (typeof ResizeObserver !== 'undefined') _stickyWatched = true;
}

/** The pinned bars sit under the top bar, whose height depends on how its controls wrap. */
function syncTopbarHeight() {
  syncStickyBars();
  var bar = document.getElementById('topbar');
  if (bar && document.documentElement && document.documentElement.style)
    document.documentElement.style.setProperty('--topbar-h', bar.offsetHeight + 'px');
}
