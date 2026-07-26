(function () {
  'use strict';

  var LB_ID = 'imageLightbox';
  var OPEN_CLASS = 'is-open';
  var MIN_SIZE = 80;
  var SKIP_CLASS = 'no-lightbox';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function isInside(selector, node) {
    return !!$(selector) && $(selector).contains(node);
  }
  function isEligibleImage(img) {
    if (!img || img.tagName !== 'IMG') return false;
    if (img.classList && img.classList.contains(SKIP_CLASS)) return false;
    var src = img.getAttribute('src') || '';
    if (!src || src.startsWith('data:')) return false;
    if (img.closest('a[href]:not([data-lightbox])')) return false;
    var rect = img.getBoundingClientRect();
    if (rect.width < MIN_SIZE || rect.height < MIN_SIZE) return false;
    return true;
  }

  function init() {
    var lb = document.getElementById(LB_ID);
    if (!lb) return;
    var imgEl = document.getElementById('lightboxImage');
    var capEl = lb.querySelector('.image-lightbox__caption');
    var closeBtn = document.getElementById('lightboxClose');
    if (!imgEl) return;

    var lastFocus = null;

    function close() {
      if (!lb.classList.contains(OPEN_CLASS)) return;
      lb.classList.remove(OPEN_CLASS);
      lb.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('lightbox-open');
      imgEl.removeAttribute('src');
      if (capEl) capEl.textContent = '';
      if (lastFocus && typeof lastFocus.focus === 'function') {
        try { lastFocus.focus({ preventScroll: true }); } catch (e) {}
      }
    }

    function open(src, alt) {
      if (!src) return;
      imgEl.src = src;
      imgEl.alt = alt || '';
      if (capEl) capEl.textContent = alt || '';
      lastFocus = document.activeElement;
      lb.classList.add(OPEN_CLASS);
      lb.setAttribute('aria-hidden', 'false');
      document.body.classList.add('lightbox-open');
      if (closeBtn) closeBtn.focus({ preventScroll: true });
    }

    function openFromImg(img) {
      var src = img.currentSrc || img.getAttribute('src');
      var alt = img.getAttribute('alt') || img.getAttribute('title') || '';
      open(src, alt);
    }

    document.addEventListener('click', function (e) {
      var target = e.target;
      if (!target || target.nodeType !== 1) return;

      if (lb.classList.contains(OPEN_CLASS)) {
        if (closeBtn && (target === closeBtn || closeBtn.contains(target))) {
          e.preventDefault();
          close();
          return;
        }
        var fig = $('.image-lightbox__figure', lb);
        if (fig && fig.contains(target)) return;
        if (lb.contains(target)) {
          e.preventDefault();
          close();
          return;
        }
      }

      if (target.tagName === 'IMG' && !isInside('#' + LB_ID, target)) {
        if (isEligibleImage(target)) {
          e.preventDefault();
          openFromImg(target);
          return;
        }
      }

      var trigger = target.closest && target.closest(
        '[data-lightbox-src], img[data-lightbox], a[data-lightbox]'
      );
      if (trigger && !isInside('#' + LB_ID, trigger)) {
        e.preventDefault();
        var src = trigger.getAttribute('data-lightbox-src')
               || (trigger.tagName === 'A' && trigger.getAttribute('href'))
               || (trigger.tagName === 'IMG' && trigger.getAttribute('src'));
        var alt = trigger.getAttribute('data-lightbox-alt')
               || (trigger.tagName === 'IMG' && trigger.getAttribute('alt'))
               || trigger.getAttribute('title') || '';
        open(src, alt);
      }
    });

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
