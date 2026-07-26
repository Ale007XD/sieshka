(function () {
  'use strict';

  var TRIGGER_SELECTOR = '[data-lightbox], a[data-lightbox-img], img[data-lightbox]';
  var OPEN_CLASS = 'is-open';

  function $(sel, root) { return (root || document).querySelector(sel); }

  function init() {
    var lb = document.getElementById('imageLightbox');
    if (!lb) return;
    var imgEl = document.getElementById('lightboxImage');
    var capEl = lb.querySelector('.image-lightbox__caption');
    var closeBtn = document.getElementById('lightboxClose');
    if (!imgEl) return;

    function close() {
      if (!lb.classList.contains(OPEN_CLASS)) return;
      lb.classList.remove(OPEN_CLASS);
      lb.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('lightbox-open');
      imgEl.removeAttribute('src');
      if (capEl) capEl.textContent = '';
    }

    function open(src, alt) {
      if (!src) return;
      imgEl.src = src;
      imgEl.alt = alt || '';
      if (capEl) capEl.textContent = alt || '';
      lb.classList.add(OPEN_CLASS);
      lb.setAttribute('aria-hidden', 'false');
      document.body.classList.add('lightbox-open');
      if (closeBtn) closeBtn.focus({ preventScroll: true });
    }

    function openFromTrigger(trigger) {
      var src = trigger.getAttribute('data-lightbox-src')
             || trigger.getAttribute('href')
             || trigger.getAttribute('src');
      var alt = trigger.getAttribute('data-lightbox-alt')
              || trigger.getAttribute('alt')
              || trigger.getAttribute('data-lightbox-caption')
              || trigger.getAttribute('aria-label');
      open(src, alt);
    }

    document.addEventListener('click', function (e) {
      var target = e.target;
      var trigger = target.closest && target.closest(TRIGGER_SELECTOR);
      if (trigger) {
        e.preventDefault();
        openFromTrigger(trigger);
        return;
      }
      if (lb.classList.contains(OPEN_CLASS)) {
        var fig = $('.image-lightbox__figure', lb);
        if (fig && fig.contains(target)) return;
        close();
      }
    });

    if (closeBtn) closeBtn.addEventListener('click', close);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && lb.classList.contains(OPEN_CLASS)) {
        e.preventDefault();
        close();
      }
    });

    lb.setAttribute('aria-hidden', 'true');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
