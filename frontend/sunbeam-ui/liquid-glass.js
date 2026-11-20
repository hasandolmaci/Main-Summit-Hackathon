/* Sunbeam · Liquid Glass helpers
   - The specular highlight follows the pointer on any .lg-glass or .lg-btn--primary.
   - Segmented controls (.lg-seg) toggle aria-pressed on their buttons.
   No dependencies. Safe to include once per page. */
(function () {
  var GLASS = '.lg-glass, .lg-btn--primary';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function track(e) {
    var el = e.target.closest && e.target.closest(GLASS);
    if (!el || reduce) return;
    var r = el.getBoundingClientRect();
    el.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
    el.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
  }
  function reset(e) {
    var el = e.target.closest && e.target.closest(GLASS);
    if (!el || (e.relatedTarget && el.contains(e.relatedTarget))) return;
    el.style.removeProperty('--mx');
    el.style.removeProperty('--my');
  }
  document.addEventListener('pointermove', track, { passive: true });
  document.addEventListener('pointerout', reset, { passive: true });

  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.lg-seg button');
    if (!b) return;
    b.parentElement.querySelectorAll('button').forEach(function (x) {
      x.setAttribute('aria-pressed', String(x === b));
    });
    b.parentElement.dispatchEvent(new CustomEvent('lg-change', { bubbles: true, detail: { value: b.dataset.value } }));
  });
})();
