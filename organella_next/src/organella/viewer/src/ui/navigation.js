var SUBNAV = [['s-overview', 'Overview'], ['s-3d', 'The cell in 3D'],
              ['s-entities', 'Structures'], ['s-groups', 'Contact groups'],
              ['s-polarity', 'Polarity'], ['s-structure', 'Files']];

/** The section nav under the top bar: only the sections there are, numbered as they are. */
function buildSubnav() {
  var nav = document.getElementById('subnav');
  if (!nav) return;
  nav.replaceChildren();
  var docked = document.getElementById('main-content').classList.contains('docked');
  var count = 0;
  SUBNAV.forEach(function(entry) {
    var section = document.getElementById(entry[0]);
    // The viewer is a column beside the page when there is geometry, and not a section of it.
    if (!section || section.classList.contains('d-none') || (entry[0] === 's-3d' && docked)) return;
    count++;
    var link = document.createElement('a');
    link.href = '#' + entry[0];
    link.dataset.target = entry[0];
    link.innerHTML = '<span class="num">' + (count < 10 ? '0' : '') + count + '</span>'
      + escHtml(entry[1]);
    link.addEventListener('click', function(event) {
      event.preventDefault();
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    nav.appendChild(link);
  });
  markSection();
}

/** Which section the reader is in: the last one whose top has passed the bar. */
function markSection() {
  var nav = document.getElementById('subnav');
  if (!nav || !nav.querySelectorAll) return;
  var edge = (document.getElementById('topbar') || { offsetHeight: 0 }).offsetHeight + 40;
  var current = null;
  nav.querySelectorAll('a').forEach(function(link) {
    var section = document.getElementById(link.dataset.target);
    if (section && section.getBoundingClientRect().top <= edge) current = link;
  });
  nav.querySelectorAll('a').forEach(function(link) {
    link.classList.toggle('active', link === (current || nav.querySelector('a')));
  });
}

var _markQueued = false;

// Not in node, where the page's own code is run to be tested and there is no window.
if (typeof window !== 'undefined' && window.addEventListener) {
  window.addEventListener('scroll', function() {
    if (_markQueued) return;
    _markQueued = true;
    requestAnimationFrame(function(){ _markQueued = false; markSection(); });
  }, { passive: true });
}
