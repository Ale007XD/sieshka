/**
 * Unified Cart System for Sieshka Food Delivery
 * Compatible with: menu.html, cart.html, checkout.html
 * Storage format: [{product_id, price_rub, name, qty, lead_time_minutes}, ...]
 */

const CartManager = (function () {
  'use strict';

  const STORAGE_KEY = 'cart';
  const QTY_MIN = 0;
  const QTY_MAX = 20;
  const MAX_ITEMS = 50;

  let offcanvasInstance = null;
  const HISTORY_KEY = 'cart_history';
  let recentlyDeleted = loadHistory();
  const upsellSuggestions = [];
  // Server-validated promo effect from POST /api/promo/check. null = none
  // applied (или invalid, или input изменён после последней проверки —
  // см. wirePromoWidget()). Единственный источник правды для скидки на
  // checkout: считается заново из subtotal/isPickup при updateCheckoutTotal(),
  // ничего не пересчитывается локально из discount%/rub без сервера.
  let appliedPromo = null; // {code, discount_rub, free_delivery}
  const zoneDeliveryFeeCache = {};
  const toastQueue = [];
  let activeToasts = [];
  const MAX_TOASTS = 3;
  const TOAST_DURATION = 4000;
  let addForLaterItems = [];
  let _clearBackup = null;
  let _clearBackupTimeout = null;
  let _eventsSetup = false;

  function loadHistory() {
    try {
      const stored = localStorage.getItem(HISTORY_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (e) {
      return [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(recentlyDeleted.slice(0, 3)));
    } catch (e) {
      console.error('Error saving history:', e);
    }
  }

  async function loadDeliveryFee(zoneId) {
    let fee = 0;
    try {
      const response = await fetch(`/api/config/delivery-fee?zone_id=${zoneId}`);
      if (response.ok) {
        const data = await response.json();
        fee = data.delivery_fee || 0;
      }
    } catch (e) {
      console.error('Error loading delivery fee:', e);
      fee = 0;
    }
    zoneDeliveryFeeCache[zoneId] = fee;
    return fee;
  }

  // 2026-08-04: zoneId=null branch removed — the last caller relying on the
  // flat settings.DELIVERY_FEE fallback (globalDeliveryFee/
  // globalDeliveryFeeLoaded) was replaced this same session with explicit
  // "depends on zone" messaging (updateOffcanvasCart/renderCartPage/
  // updateCheckoutTotal). Every remaining call site always has a real
  // zoneId; a null/undefined zoneId here now means "fee not known yet",
  // which is the caller's job to handle (don't guess), not this function's.
  async function getDeliveryFee(zoneId) {
    if (!zoneId) return 0;
    if (Object.prototype.hasOwnProperty.call(zoneDeliveryFeeCache, zoneId)) {
      return zoneDeliveryFeeCache[zoneId];
    }
    return await loadDeliveryFee(zoneId);
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function formatPrice(price) {
    return Math.round(price).toLocaleString('ru-RU') + ' ₽';
  }

  function normalizeLeadTime(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? Math.round(n) : 0;
  }

  function loadCart() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      const cart = stored ? JSON.parse(stored) : [];
      if (!Array.isArray(cart)) return [];

      return cart.map(item => ({
        ...item,
        lead_time_minutes: normalizeLeadTime(item.lead_time_minutes)
      }));
    } catch (e) {
      console.error('Error loading cart:', e);
      return [];
    }
  }

  function saveCart(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch (e) {
      console.error('Error saving cart:', e);
    }
  }

  function findItemIndex(items, productId) {
    return items.findIndex(item => item.product_id === productId);
  }

  function getTotalItems(items) {
    return items.reduce((sum, item) => sum + item.qty, 0);
  }

  function getTotalPrice(items) {
    return items.reduce((sum, item) => sum + (item.price_rub * item.qty), 0);
  }

  function getMaxLeadTime() {
    const items = loadCart();
    return items.reduce((maxLead, item) => {
      return Math.max(maxLead, normalizeLeadTime(item.lead_time_minutes));
    }, 0);
  }

  function trackDeleted(item) {
    const existing = recentlyDeleted.findIndex(x => x.product_id === item.product_id);
    if (existing >= 0) recentlyDeleted.splice(existing, 1);

    recentlyDeleted.unshift({
      product_id: item.product_id,
      name: item.name,
      price_rub: item.price_rub
    });

    if (recentlyDeleted.length > 3) recentlyDeleted.pop();
    saveHistory();
  }

  function addItem(productId, priceRub, name, leadTimeMinutes = 0) {
    const items = loadCart();
    const idx = findItemIndex(items, productId);
    const totalItems = getTotalItems(items);
    const normalizedLeadTime = normalizeLeadTime(leadTimeMinutes);

    if (idx >= 0) {
      const newQty = items[idx].qty + 1;
      if (newQty > QTY_MAX) {
        showToast('Достигнут лимит: макс. 20 шт. на товар', 'warning');
        return false;
      }
      items[idx].qty = newQty;
      items[idx].lead_time_minutes = Math.max(
        normalizeLeadTime(items[idx].lead_time_minutes),
        normalizedLeadTime
      );
    } else {
      if (totalItems >= MAX_ITEMS) {
        showToast(`Максимум ${MAX_ITEMS} товаров в корзине`, 'warning');
        return false;
      }

      items.push({
        product_id: productId,
        price_rub: priceRub,
        name: name,
        qty: 1,
        lead_time_minutes: normalizedLeadTime
      });

      const rdIdx = recentlyDeleted.findIndex(x => x.product_id === productId);
      if (rdIdx >= 0) recentlyDeleted.splice(rdIdx, 1);
    }

    saveCart(items);
    updateAllUI();
    return true;
  }

  function updateQty(productId, delta) {
    const items = loadCart();
    const idx = findItemIndex(items, productId);

    if (idx < 0 && delta > 0) {
      console.warn('Cannot add new item via updateQty - use addItem');
      return false;
    }

    if (idx >= 0) {
      const newQty = items[idx].qty + delta;

      if (newQty <= 0) {
        trackDeleted(items[idx]);
        items.splice(idx, 1);
      } else if (newQty > QTY_MAX) {
        showToast('Достигнут лимит: макс. 20 шт. на товар', 'warning');
        return false;
      } else {
        const totalItems = getTotalItems(items);
        if (delta > 0 && totalItems >= MAX_ITEMS) {
          showToast(`Максимум ${MAX_ITEMS} товаров в корзине`, 'warning');
          return false;
        }
        items[idx].qty = newQty;
      }

      saveCart(items);
      updateAllUI();
      return true;
    }

    return false;
  }

  function setQty(productId, priceRub, name, qty, leadTimeMinutes = 0) {
    qty = Math.max(QTY_MIN, Math.min(QTY_MAX, parseInt(qty, 10) || 0));

    const items = loadCart();
    const idx = findItemIndex(items, productId);
    const normalizedLeadTime = normalizeLeadTime(leadTimeMinutes);

    if (qty <= 0) {
      if (idx >= 0) {
        items.splice(idx, 1);
      }
    } else {
      if (idx >= 0) {
        items[idx].qty = qty;
        items[idx].lead_time_minutes = Math.max(
          normalizeLeadTime(items[idx].lead_time_minutes),
          normalizedLeadTime
        );
      } else {
        items.push({
          product_id: productId,
          price_rub: priceRub,
          name: name,
          qty: qty,
          lead_time_minutes: normalizedLeadTime
        });
      }
    }

    const totalItems = getTotalItems(items);
    if (totalItems > MAX_ITEMS) {
      showToast(`Максимум ${MAX_ITEMS} товаров в корзине`, 'warning');
      return false;
    }

    saveCart(items);
    updateAllUI();
    return true;
  }

  function removeItem(productId) {
    const items = loadCart();
    const idx = findItemIndex(items, productId);

    if (idx >= 0) {
      trackDeleted(items[idx]);
      items.splice(idx, 1);
      saveCart(items);
      updateAllUI();
      return true;
    }

    return false;
  }

  function clearCart() {
    const items = loadCart();
    if (items.length === 0) return true;

    _clearBackup = JSON.stringify(items);
    if (_clearBackupTimeout) clearTimeout(_clearBackupTimeout);

    _clearBackupTimeout = setTimeout(() => {
      _clearBackup = null;
    }, 5000);

    saveCart([]);
    updateAllUI();
    showUndoToast('Корзина очищена', undoClearCart);
    return true;
  }

  function undoClearCart() {
    if (!_clearBackup) return false;

    clearTimeout(_clearBackupTimeout);
    saveCart(JSON.parse(_clearBackup));
    _clearBackup = null;
    updateAllUI();
    return true;
  }

  function getItemQty(productId) {
    const items = loadCart();
    const idx = findItemIndex(items, productId);
    return idx >= 0 ? items[idx].qty : 0;
  }

  function getItems() {
    return loadCart().map(item => ({
      product_id: item.product_id,
      qty: item.qty
    }));
  }

  function pluralizeTovar(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 14) return 'товаров';
    if (mod10 === 1) return 'товар';
    if (mod10 >= 2 && mod10 <= 4) return 'товара';
    return 'товаров';
  }

  // 2026-08-04 fix: stickyCartBar/navbarCart total was stuck on the flat
  // globalDeliveryFee cache, which nothing populates anymore (that fallback
  // path was removed this session) — the footer sum silently stopped
  // including delivery at all and never reacted to #f-zone on checkout.
  // Shared here so both widgets follow the same "don't guess" rule as
  // updateCheckoutTotal: 0 on pickup, 0 (goods-only) until a zone is
  // actually selected, real per-zone fee once it is. Also now respects
  // appliedPromo.free_delivery (was ignored here — a free-delivery promo
  // updated the checkout order summary but not these two widgets).
  async function _widgetDeliveryFee(totalItems) {
    if (totalItems === 0) return 0;
    const pickupEl = document.getElementById('delivery_pickup');
    if (pickupEl && pickupEl.checked) return 0;
    if (appliedPromo && appliedPromo.free_delivery) return 0;
    const zoneSelect = document.getElementById('f-zone');
    const zoneId = zoneSelect ? (zoneSelect.value || null) : null;
    if (!zoneId) return 0;
    return await getDeliveryFee(zoneId);
  }

  async function updateStickyCartBar() {
    const bar = document.getElementById('stickyCartBar');
    if (!bar) return;

    const items = loadCart();
    const totalItems = getTotalItems(items);
    const subtotal = getTotalPrice(items);
    // 2026-08-04 fix: discountRub was never applied here — a promo code
    // updated the checkout order summary but not this bar. Mirrors the
    // Math.max(0, subtotal - discountRub) done in updateCheckoutTotal.
    const discountRub = appliedPromo ? Math.min(appliedPromo.discount_rub, subtotal) : 0;
    const currentDeliveryFee = await _widgetDeliveryFee(totalItems);
    const totalPrice = Math.max(0, subtotal - discountRub) + currentDeliveryFee;

    bar.classList.toggle('visible', totalItems > 0);
    document.body.classList.toggle('has-sticky-cartbar', totalItems > 0);

    if (totalItems === 0) return;

    const countEl = document.getElementById('stickyCartBarCount');
    const labelEl = document.getElementById('stickyCartBarLabel');
    const totalEl = document.getElementById('stickyCartBarTotal');
    if (countEl) countEl.textContent = totalItems;
    if (labelEl) labelEl.textContent = pluralizeTovar(totalItems);
    if (totalEl) totalEl.textContent = formatPrice(totalPrice);
  }

  async function updateNavbarCart() {
    const items = loadCart();
    const totalItems = getTotalItems(items);
    const subtotal = getTotalPrice(items);
    // 2026-08-04 fix: same discountRub gap as updateStickyCartBar above.
    const discountRub = appliedPromo ? Math.min(appliedPromo.discount_rub, subtotal) : 0;
    const currentDeliveryFee = await _widgetDeliveryFee(totalItems);
    const totalPrice = Math.max(0, subtotal - discountRub) + currentDeliveryFee;

    const summaryEl = document.getElementById('navbarCartSummary');
    if (summaryEl) {
      summaryEl.textContent = totalItems > 0 ? `${totalItems} · ${formatPrice(totalPrice)}` : '';
    }

    const badgeEl = document.getElementById('navbarCartBadge');
    if (badgeEl) {
      if (totalItems > 0) {
        badgeEl.textContent = totalItems > 99 ? '99+' : totalItems;
        badgeEl.classList.remove('d-none');
      } else {
        badgeEl.classList.add('d-none');
      }
    }

    const legacyBadge = document.getElementById('cartBadge');
    if (legacyBadge) {
      if (totalItems > 0) {
        legacyBadge.textContent = totalItems > 99 ? '99+' : totalItems;
        legacyBadge.classList.remove('d-none');
      } else {
        legacyBadge.classList.add('d-none');
      }
    }

    const menuCartTotal = document.getElementById('cart-total');
    if (menuCartTotal) {
      menuCartTotal.textContent = formatPrice(totalPrice);
    }

    const menuCartBadge = document.getElementById('cart-badge');
    if (menuCartBadge) {
      menuCartBadge.textContent = totalItems;
      menuCartBadge.style.display = totalItems > 0 ? 'inline' : 'none';
    }

    // Обновляем контрактные элементы (menu.js contract compliance)
    const cartRoot = document.getElementById('cart-root');
    if (cartRoot) {
      cartRoot.style.display = totalItems > 0 ? '' : 'none';
    }
    const cartSummary = document.getElementById('cart-summary');
    if (cartSummary) {
      cartSummary.textContent = totalItems > 0 ? formatPrice(totalPrice) : '';
    }
    const cartCount = document.getElementById('cart-count');
    if (cartCount) {
      cartCount.textContent = totalItems;
    }
    // toast-root: гарантируем наличие контейнера тостов
    const toastRoot = document.getElementById('toast-root');
    if (toastRoot && !toastRoot.querySelector('.toast-container')) {
      const tc = document.createElement('div');
      tc.className = 'toast-container';
      toastRoot.appendChild(tc);
    }
  }

  async function updateOffcanvasCart() {
    const items = loadCart();
    const container = document.getElementById('offcanvasCartItems');
    const emptyMessage = document.getElementById('offcanvasEmptyMessage');
    const footer = document.getElementById('offcanvasCartFooter');
    const subtotalEl = document.getElementById('offcanvasCartSubtotal');
    const deliveryEl = document.getElementById('offcanvasCartDeliveryFee');
    const totalEl = document.getElementById('offcanvasCartTotal');

    if (!container) return;

    if (items.length === 0 && recentlyDeleted.length === 0) {
      container.classList.add('d-none');
      container.innerHTML = '';
      if (emptyMessage) emptyMessage.classList.remove('d-none');
      if (footer) footer.classList.add('d-none');
      return;
    }

    container.classList.remove('d-none');
    if (emptyMessage) emptyMessage.classList.add('d-none');
    if (footer) footer.classList.remove('d-none');

    let html = '<div class="cart-items-list">';
    let subtotal = 0;

    items.forEach(item => {
      const itemTotal = item.price_rub * item.qty;
      subtotal += itemTotal;

      html += `
        <div class="cart-item" data-product-id="${item.product_id}" data-name="${escapeHtml(item.name)}" data-price="${item.price_rub}" data-lead-time-minutes="${normalizeLeadTime(item.lead_time_minutes)}">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div style="flex: 1; min-width: 0;">
              <div class="cart-item-name text-truncate fw-bold">${escapeHtml(item.name)}</div>
              <div class="cart-item-price small text-muted">${formatPrice(item.price_rub)}/шт</div>
            </div>
            <div class="cart-item-total fw-bold text-brand">${formatPrice(itemTotal)}</div>
          </div>
          <div class="d-flex justify-content-between align-items-center">
            <div class="qty-control">
              <button type="button" class="qty-btn" data-action="dec">−</button>
              <span class="qty-value">${item.qty}</span>
              <button type="button" class="qty-btn" data-action="inc" ${item.qty >= QTY_MAX ? 'disabled' : ''}>+</button>
            </div>
            <button type="button" class="btn btn-sm text-danger p-0" data-action="remove" style="font-size: 0.8rem;">
              <i class="bi bi-trash me-1"></i>Удалить
            </button>
          </div>
        </div>
      `;
    });

    html += '</div>';

    const filteredUpsell = upsellSuggestions
      .filter(u => !items.some(i => i.product_id === u.product_id))
      .slice(0, 3);

    if (filteredUpsell.length > 0) {
      html += `
        <div class="upsell-section mt-4 bg-light p-3 rounded-4 mx-2">
          <div class="small text-muted text-uppercase fw-bold mb-2" style="font-size: 0.7rem;">Не забудьте добавить:</div>
          <div class="d-flex flex-column gap-2">
            ${filteredUpsell.map(u => `
              <div class="d-flex justify-content-between align-items-center" data-product-id="${u.product_id}" data-name="${escapeHtml(u.name)}" data-price="${u.price_rub}" data-lead-time-minutes="${normalizeLeadTime(u.lead_time_minutes)}">
                <div style="flex: 1; min-width: 0;">
                  <div class="small text-truncate fw-semibold">${escapeHtml(u.name)}</div>
                  <div class="small text-brand">${formatPrice(u.price_rub)}</div>
                </div>
                <button type="button" class="btn btn-sm btn-brand rounded-pill px-3" data-action="add" style="font-size: 0.75rem;">
                  +
                </button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    const laterItems = getAddForLaterItems()
      .filter(u => !items.some(i => i.product_id === u.product_id));

    if (laterItems.length > 0) {
      html += `
        <div class="add-for-later-section mt-4 p-3 rounded-4 mx-2" style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); border: 1px dashed var(--color-accent-border);">
          <div class="small text-muted text-uppercase fw-bold mb-2" style="font-size: 0.7rem;">
            <i class="bi bi-clock-history me-1"></i>Добавить на позже:
          </div>
          <div class="d-flex flex-column gap-2">
            ${laterItems.slice(0, 3).map(u => `
              <div class="d-flex justify-content-between align-items-center" data-product-id="${u.product_id}" data-name="${escapeHtml(u.name)}" data-price="${u.price_rub}" data-lead-time-minutes="${normalizeLeadTime(u.lead_time_minutes)}">
                <div style="flex: 1; min-width: 0;">
                  <div class="small text-truncate fw-semibold">${escapeHtml(u.name)}</div>
                  <div class="small text-brand">${formatPrice(u.price_rub)}</div>
                  <div class="small text-muted" style="font-size: 0.65rem;">
                    <i class="bi bi-calendar-event me-1"></i>${escapeHtml(u.next_available || '')}
                  </div>
                </div>
                <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill px-3" data-action="add" style="font-size: 0.75rem;" title="Добавить в корзину (будет доступно позже)">
                  <i class="bi bi-plus"></i>
                </button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    if (recentlyDeleted.length > 0) {
      html += `
        <div class="recently-deleted-section mt-4 px-2">
          <div class="small text-muted text-uppercase fw-bold mb-2 px-1" style="font-size: 0.7rem;">Недавно удаленные:</div>
          <div class="d-flex flex-column gap-1">
            ${recentlyDeleted.map(rd => `
              <div class="d-flex justify-content-between align-items-center py-2 border-bottom border-light opacity-75" data-product-id="${rd.product_id}" data-name="${escapeHtml(rd.name)}" data-price="${rd.price_rub}">
                <div style="flex: 1; min-width: 0;">
                  <div class="small text-truncate text-muted">${escapeHtml(rd.name)}</div>
                </div>
                <button type="button" class="btn btn-sm btn-outline-secondary border-0 rounded-pill px-2" data-action="restore" style="font-size: 0.7rem;">
                  <i class="bi bi-arrow-counterclockwise"></i> Вернуть
                </button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    container.innerHTML = html;

    // 2026-08-04 fix: this page has no zone selector — the zone is chosen
    // only at checkout. Fetching+showing settings.DELIVERY_FEE here was a
    // guess dressed up as a number (577); replaced with an explicit "depends
    // on zone" message, no network call, no false precision. Grand total
    // reflects goods only until the zone is known.
    const grandTotal = subtotal;

    if (subtotalEl) subtotalEl.textContent = formatPrice(subtotal);
    if (deliveryEl) deliveryEl.textContent = subtotal > 0 ? 'Будет зависеть от зоны' : '0 ₽';
    if (totalEl) totalEl.textContent = formatPrice(grandTotal);
  }

  function updateProductControls() {
    const menuContainer = document.getElementById('menu-container');
    if (!menuContainer) return;

    const controls = menuContainer.querySelectorAll('.product-card[data-product-id]');
    controls.forEach(el => {
      const productId = el.dataset.productId;
      if (!productId) return;

      const qty = getItemQty(productId);
      const qtyDisplay = el.querySelector('.qty-display');
      if (qtyDisplay) qtyDisplay.textContent = qty;

      const addBtn = el.querySelector('.btn-add-to-cart');
      const qtyControl = el.querySelector('.product-controls');

      if (addBtn && qtyControl) {
        if (qty > 0) {
          addBtn.classList.add('d-none');
          qtyControl.classList.remove('d-none');
          qtyControl.classList.add('d-flex');
          const plusBtn = qtyControl.querySelector('.qty-btn:last-child');
          if (plusBtn) {
            plusBtn.disabled = qty >= QTY_MAX;
          }
        } else {
          addBtn.classList.remove('d-none');
          qtyControl.classList.add('d-none');
          qtyControl.classList.remove('d-flex');
        }
      }
    });
  }

  async function renderCartPage() {
    const container = document.getElementById('cart');
    const emptyCart = document.getElementById('emptyCart');
    const cartReal = document.getElementById('cartReal');
    const cartActions = document.getElementById('cartActions');

    if (!container) return;

    const items = loadCart();

    if (items.length === 0) {
      container.innerHTML = '';
      if (emptyCart) emptyCart.classList.remove('d-none');
      if (cartReal) cartReal.classList.add('d-none');
      if (cartActions) cartActions.classList.add('d-none');
      return;
    }

    if (emptyCart) emptyCart.classList.add('d-none');
    if (cartReal) cartReal.classList.remove('d-none');
    if (cartActions) cartActions.classList.remove('d-none');

    let html = '<div class="vstack gap-3">';
    let subtotal = 0;

    items.forEach(item => {
      const itemTotal = item.price_rub * item.qty;
      subtotal += itemTotal;

      html += `
        <div class="d-flex justify-content-between align-items-center py-2 border-bottom" data-product-id="${item.product_id}" data-name="${escapeHtml(item.name)}" data-price="${item.price_rub}" data-lead-time-minutes="${normalizeLeadTime(item.lead_time_minutes)}">
          <div style="flex: 1; min-width: 0;">
            <div class="fw-semibold text-truncate">${escapeHtml(item.name)}</div>
            <div class="text-muted small">${formatPrice(item.price_rub)}/шт</div>
          </div>
          <div class="d-flex align-items-center gap-2" style="flex-shrink: 0;">
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-circle" style="width: 32px; height: 32px; padding: 0;" data-action="dec">−</button>
            <span class="fw-semibold" style="min-width: 28px; text-align: center;">${item.qty}</span>
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-circle" style="width: 32px; height: 32px; padding: 0;" data-action="inc" ${item.qty >= QTY_MAX ? 'disabled' : ''}>+</button>
          </div>
          <div class="fw-bold ms-3" style="min-width: 80px; text-align: right;">${formatPrice(itemTotal)}</div>
        </div>
      `;
    });

    html += '</div>';

    // 2026-08-04 fix: same as updateOffcanvasCart above — no zone selector
    // on this page, flat-fee fetch replaced with explicit "depends on zone"
    // message. Grand total = goods only until zone is known at checkout.
    const grandTotal = subtotal;

    html += `
      <div class="cart-totals mt-3 pt-3 border-top">
        <div class="d-flex justify-content-between mb-2">
          <span class="text-muted">Итого (товары):</span>
          <span class="fw-semibold" id="cartPageSubtotal">${formatPrice(subtotal)}</span>
        </div>
        <div class="d-flex justify-content-between mb-2">
          <span class="text-muted">Доставка:</span>
          <span class="fw-semibold" id="cartPageDeliveryFee">Будет зависеть от зоны</span>
        </div>
        <div class="d-flex justify-content-between fw-bold h5 mb-0 mt-2 pt-2 border-top">
          <span>Итого к оплате:</span>
          <span class="text-brand" id="cartPageGrandTotal">${formatPrice(grandTotal)}</span>
        </div>
      </div>
    `;

    container.innerHTML = html;
  }

  async function updateCheckoutTotal(isPickup) {
    // Reads the checkout PAGE's own totals (#checkoutSubtotal etc, in
    // checkout.html's own static markup) — separate namespace from
    // renderCartPage()'s #cartPageSubtotal etc above (2026-08-01 fix: these
    // used to share ids, and since renderCartPage() runs first via
    // CartManager.init() and writes into checkout.html's hidden leftover
    // <div id="cart" class="d-none"> stub — which sits earlier in the DOM —
    // every getElementById('checkoutSubtotal') resolved to that invisible
    // duplicate instead of the page's actual visible totals block).
    const subtotalEl = document.getElementById('checkoutSubtotal');
    const deliveryEl = document.getElementById('checkoutDeliveryFee');
    const grandTotalEl = document.getElementById('checkoutGrandTotal');
    const discountRowEl = document.getElementById('discountRow');
    const discountEl = document.getElementById('checkoutDiscount');
    const promoCodeEl = document.getElementById('checkoutPromoCode');

    if (subtotalEl || deliveryEl || grandTotalEl) {
      const items = loadCart();
      const subtotal = getTotalPrice(items);
      // 2026-08-04 fix: zoneId was never read here — this function always
      // fetched the flat settings.DELIVERY_FEE fallback (zone_id=null),
      // regardless of the zone actually selected in #f-zone. Meanwhile a
      // separate inline <script> in checkout.html duplicated this same
      // calculation zone-aware-but-promo-blind, writing into the SAME
      // #checkoutDeliveryFee/#checkoutGrandTotal elements — whichever ran
      // last won, non-deterministically. That duplicate calc has been
      // removed from checkout.html; this function is now the single source
      // of truth for checkout totals (zone AND promo aware).
      const zoneSelect = document.getElementById('f-zone');
      const zoneId = zoneSelect ? (zoneSelect.value || null) : null;
      const zoneKnown = !isPickup && !!zoneId;

      const discountRub = appliedPromo ? Math.min(appliedPromo.discount_rub, subtotal) : 0;
      const freeDelivery = !!(appliedPromo && appliedPromo.free_delivery);

      // 2026-08-04: showing settings.DELIVERY_FEE (flat fallback) before a
      // zone is picked was a guess dressed up as a real number — replaced
      // with an explicit "depends on zone" message, and the guessed fee is
      // no longer added into the grand total until the zone is actually
      // known (or pickup/free-delivery-promo makes it moot regardless).
      let effectiveFee = 0;
      let deliveryText;
      if (isPickup) {
        deliveryText = '0 ₽ (самовывоз)';
      } else if (freeDelivery) {
        deliveryText = formatPrice(0);
      } else if (!zoneKnown) {
        deliveryText = 'Будет зависеть от зоны';
      } else {
        effectiveFee = await getDeliveryFee(zoneId);
        deliveryText = formatPrice(effectiveFee);
      }

      const grandTotal = Math.max(0, subtotal - discountRub) + effectiveFee;

      if (subtotalEl) subtotalEl.textContent = formatPrice(subtotal);
      if (deliveryEl) deliveryEl.textContent = deliveryText;
      if (grandTotalEl) grandTotalEl.textContent = formatPrice(grandTotal);

      if (discountRowEl && discountEl) {
        if (appliedPromo && (discountRub > 0 || appliedPromo.free_delivery)) {
          discountRowEl.classList.remove('d-none');
          discountEl.textContent = discountRub > 0 ? `−${formatPrice(discountRub)}` : 'бесплатная доставка';
          if (promoCodeEl) promoCodeEl.textContent = appliedPromo.code;
        } else {
          discountRowEl.classList.add('d-none');
        }
      }
    }
  }

  function renderRecentlyDeletedOnCheckout() {
    const container = document.getElementById('recentlyDeletedList');
    if (!container) return;

    if (recentlyDeleted.length === 0) {
      container.innerHTML = '';
      const section = container.closest('.recently-deleted-section');
      if (section) section.style.display = 'none';
      return;
    }

    const section = container.closest('.recently-deleted-section');
    if (section) section.style.display = 'block';

    let html = '';
    recentlyDeleted.forEach(rd => {
      html += `
        <div class="d-flex justify-content-between align-items-center py-2 border-bottom border-light opacity-75" data-product-id="${rd.product_id}" data-name="${escapeHtml(rd.name)}" data-price="${rd.price_rub}">
          <div style="flex: 1; min-width: 0;">
            <div class="small text-truncate text-muted">${escapeHtml(rd.name)}</div>
          </div>
          <button type="button" class="btn btn-sm btn-outline-secondary border-0 rounded-pill px-2" data-action="restore" style="font-size: 0.7rem;">
            <i class="bi bi-arrow-counterclockwise"></i> Вернуть
          </button>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function renderRecentlyDeletedOnCart() {
    const section = document.getElementById('recentlyDeletedSection');
    const container = document.getElementById('recentlyDeletedList');
    if (!container || !section) return;

    if (recentlyDeleted.length === 0) {
      section.classList.add('d-none');
      return;
    }

    section.classList.remove('d-none');

    let html = '';
    recentlyDeleted.slice(0, 3).forEach(rd => {
      html += `
        <div class="d-flex justify-content-between align-items-center py-2 border-bottom border-light opacity-75" data-product-id="${rd.product_id}" data-name="${escapeHtml(rd.name)}" data-price="${rd.price_rub}">
          <div style="flex: 1; min-width: 0;">
            <div class="small text-truncate text-muted">${escapeHtml(rd.name)}</div>
          </div>
          <button type="button" class="btn btn-sm btn-outline-secondary border-0 rounded-pill px-2" data-action="restore" style="font-size: 0.7rem;">
            <i class="bi bi-arrow-counterclockwise"></i> Вернуть
          </button>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  // 2026-08-04 fix: the "Ваш заказ" item list on checkout (#orderItemsList)
  // used to be built exactly once, inline in checkout.html's own
  // DOMContentLoaded handler, straight from a one-time CartManager.loadCart()
  // snapshot — never touched again afterwards. Every subsequent cart mutation
  // on that page (restore-from-recently-deleted, qty change, promo apply)
  // correctly updated the running total (updateCheckoutTotal reads the cart
  // fresh every time) but left this list frozen at whatever it showed on
  // page load — visually "clicking restore does nothing" while the total
  // silently climbed. Moved here so it participates in the same
  // updateAllUI() cycle as everything else; checkout.html no longer builds
  // this list itself.
  function renderOrderItemsList() {
    const listEl = document.getElementById('orderItemsList');
    if (!listEl) return; // not on checkout page

    const totalsEl = document.getElementById('orderTotals');
    const emptyEl = document.getElementById('checkout-empty');
    const formEl = document.getElementById('checkout-form');
    const items = loadCart();

    if (!items || items.length === 0) {
      listEl.innerHTML = '';
      if (totalsEl) totalsEl.classList.add('d-none');
      if (emptyEl) emptyEl.classList.remove('d-none');
      if (formEl) formEl.classList.add('d-none');
      return;
    }

    if (emptyEl) emptyEl.classList.add('d-none');
    if (formEl) formEl.classList.remove('d-none');

    // 2026-08-04: rows now carry +/- steppers (data-action="dec"/"inc"),
    // wired via the delegated #orderItemsList listener in
    // setupEventListeners() below — same data-product-id/data-price/
    // data-name/data-lead-time-minutes contract + updateQty()/QTY_MAX cap
    // as renderCartPage()'s item rows, for one consistent stepper behavior
    // across cart page / offcanvas / checkout.
    let html = '';
    items.forEach(function (item) {
      const lineTotal = item.price_rub * item.qty;
      html += `
        <div class="order-item-row" data-product-id="${item.product_id}" data-name="${escapeHtml(item.name)}" data-price="${item.price_rub}" data-lead-time-minutes="${normalizeLeadTime(item.lead_time_minutes)}">
          <div style="flex: 1; min-width: 0;">
            <div class="fw-semibold text-truncate small">${escapeHtml(item.name)}</div>
            <div class="text-muted small">${formatPrice(item.price_rub)}/шт</div>
          </div>
          <div class="d-flex align-items-center gap-2 mx-2" style="flex-shrink: 0;">
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-circle" style="width: 28px; height: 28px; padding: 0;" data-action="dec">−</button>
            <span class="fw-semibold small" style="min-width: 20px; text-align: center;">${item.qty}</span>
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-circle" style="width: 28px; height: 28px; padding: 0;" data-action="inc" ${item.qty >= QTY_MAX ? 'disabled' : ''}>+</button>
          </div>
          <div class="fw-semibold small" style="min-width: 70px; text-align: right;">${formatPrice(lineTotal)}</div>
        </div>
      `;
    });
    listEl.innerHTML = html;
  }

  // 2026-08-04 fix: recentlyDeleted could retain an entry for a product_id
  // that's already back in the cart (observed: a "Вернуть" prompt sitting
  // next to a cart line for the same item, already at the qty cap) — addItem()
  // only cleans this array on its idx<0 (fresh re-add) branch; any other path
  // that puts the product back in the cart (or a stale entry from before a
  // menu/product_id change) left it dangling, confusingly offering "restore
  // +1" for an item the person already has. Self-heals on every UI update
  // rather than only in the one path that happened to clean it before.
  function _pruneRecentlyDeleted() {
    if (recentlyDeleted.length === 0) return;
    const cartProductIds = new Set(loadCart().map(i => i.product_id));
    const before = recentlyDeleted.length;
    recentlyDeleted = recentlyDeleted.filter(rd => !cartProductIds.has(rd.product_id));
    if (recentlyDeleted.length !== before) saveHistory();
  }

  async function updateAllUI() {
    _pruneRecentlyDeleted();
    await updateNavbarCart();
    await updateOffcanvasCart();
    await updateStickyCartBar();
    updateProductControls();
    await renderCartPage();
    renderOrderItemsList();
    const _pickupEl = document.getElementById('delivery_pickup');
    await updateCheckoutTotal(_pickupEl && _pickupEl.checked);
    renderRecentlyDeletedOnCheckout();
    renderRecentlyDeletedOnCart();
    // Уведомить страницу оформления об изменении корзины
    // (чтобы hint с временем доставки пересчитался)
    document.dispatchEvent(new CustomEvent('cart:updated'));
  }

  function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    toastQueue.push({ message, type, timestamp: Date.now() });
    processToastQueue();
  }

  const shownUpsellIds = new Set();

  function getUpsellCandidate(excludeProductId) {
    const items = loadCart();
    return upsellSuggestions.find(u =>
      u.product_id !== excludeProductId &&
      !shownUpsellIds.has(u.product_id) &&
      !items.some(i => i.product_id === u.product_id)
    ) || null;
  }

  function showUpsellToast(excludeProductId) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const candidate = getUpsellCandidate(excludeProductId);
    if (!candidate) return;

    shownUpsellIds.add(candidate.product_id);

    const toastEl = document.createElement('div');
    toastEl.className = 'toast show';
    toastEl.setAttribute('role', 'status');
    toastEl.setAttribute('aria-live', 'polite');
    toastEl.style.cssText = 'position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 9999; min-width: 280px; max-width: 90vw; background: var(--color-bg-primary); border: 1px solid var(--color-accent-border); border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);';

    toastEl.innerHTML = `
      <div class="toast-body d-flex justify-content-between align-items-center p-3 gap-3">
        <div style="flex: 1; min-width: 0;">
          <div class="small text-muted mb-1">Часто берут вместе:</div>
          <div class="fw-semibold text-truncate">${escapeHtml(candidate.name)} · ${formatPrice(candidate.price_rub)}</div>
        </div>
        <button type="button" class="btn btn-sm btn-brand rounded-pill px-3 flex-shrink-0">Добавить</button>
      </div>
    `;

    container.appendChild(toastEl);

    const addBtn = toastEl.querySelector('button');
    if (addBtn) {
      addBtn.addEventListener('click', () => {
        addItem(candidate.product_id, candidate.price_rub, candidate.name, candidate.lead_time_minutes || 0);
        toastEl.classList.remove('show');
        setTimeout(() => toastEl.remove(), 300);
      });
    }

    setTimeout(() => {
      toastEl.classList.remove('show');
      setTimeout(() => toastEl.remove(), 300);
    }, 5000);
  }

  function showUndoToast(message, undoCallback) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toastId = 'undoToast' + Date.now();
    const toastEl = document.createElement('div');
    toastEl.className = 'toast show';
    toastEl.id = toastId;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');
    toastEl.style.cssText = 'position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 9999; min-width: 280px; background: var(--color-bg-primary); border: 1px solid var(--color-accent-border); border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);';

    toastEl.innerHTML = `
      <div class="toast-body d-flex justify-content-between align-items-center p-3">
        <span class="text-muted">${escapeHtml(message)}</span>
        <button type="button" class="btn btn-sm btn-link text-brand p-0 ms-3 fw-semibold" style="text-decoration: none;">
          Отменить
        </button>
      </div>
    `;

    container.appendChild(toastEl);

    const undoBtn = toastEl.querySelector('button');
    if (undoBtn && undoCallback) {
      undoBtn.addEventListener('click', () => {
        undoCallback();
        toastEl.classList.remove('show');
        setTimeout(() => toastEl.remove(), 300);
      });
    }

    setTimeout(() => {
      toastEl.classList.remove('show');
      setTimeout(() => toastEl.remove(), 300);
    }, 5000);
  }

  function processToastQueue() {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    activeToasts = activeToasts.filter(toast => {
      if (Date.now() - toast.timestamp > TOAST_DURATION) {
        if (toast.element && toast.element.parentNode) {
          toast.element.classList.remove('show');
          setTimeout(() => {
            if (toast.element && toast.element.parentNode) {
              toast.element.remove();
            }
          }, 300);
        }
        if (toast.timeoutId) clearTimeout(toast.timeoutId);
        return false;
      }
      return true;
    });

    while (toastQueue.length > 0 && activeToasts.length < MAX_TOASTS) {
      const toastData = toastQueue.shift();
      createAndShowToast(toastData, container);
    }
  }

  function createAndShowToast(toastData, container) {
    const { message, type } = toastData;
    const toastId = 'cartToast' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);

    let icon = 'info-circle';
    switch (type) {
      case 'success':
        icon = 'check-circle';
        break;
      case 'warning':
        icon = 'exclamation-triangle';
        break;
      case 'error':
        icon = 'x-circle';
        break;
    }

    const toastEl = document.createElement('div');
    toastEl.className = 'toast show';
    toastEl.id = toastId;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');

    toastEl.innerHTML = `
      <div class="toast-header">
        <i class="bi bi-${icon} me-2"></i>
        <strong class="me-auto">Корзина обновлена</strong>
        <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
      <div class="toast-body">
        ${escapeHtml(message)}
      </div>
    `;

    container.appendChild(toastEl);

    const closeBtn = toastEl.querySelector('.btn-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        toastEl.classList.remove('show');
        setTimeout(() => {
          if (toastEl.parentNode) toastEl.remove();
          removeFromActiveToasts(toastId);
        }, 300);
      });
    }

    const timeoutId = setTimeout(() => {
      toastEl.classList.remove('show');
      setTimeout(() => {
        if (toastEl.parentNode) toastEl.remove();
        removeFromActiveToasts(toastId);
        processToastQueue();
      }, 300);
    }, TOAST_DURATION);

    activeToasts.push({
      id: toastId,
      element: toastEl,
      timeoutId: timeoutId,
      timestamp: Date.now()
    });
  }

  function removeFromActiveToasts(toastId) {
    const index = activeToasts.findIndex(t => t.id === toastId);
    if (index > -1) {
      const toast = activeToasts[index];
      if (toast.timeoutId) clearTimeout(toast.timeoutId);
      activeToasts.splice(index, 1);
    }
  }

  function setupEventListeners() {
    if (_eventsSetup) return;
    _eventsSetup = true;

    const clearBtn = document.getElementById('offcanvasClearBtn');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        clearCart();
      });
    }

    const offcanvasBody = document.getElementById('offcanvasCartBody');
    if (offcanvasBody) {
      offcanvasBody.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        const action = target.dataset.action;
        const itemEl = target.closest('[data-product-id]');
        if (!itemEl) return;

        const productId = itemEl.dataset.productId;
        const price = parseInt(itemEl.dataset.price, 10);
        const name = itemEl.dataset.name;
        const leadTimeMinutes = parseInt(itemEl.dataset.leadTimeMinutes || '0', 10);

        switch (action) {
          case 'inc':
            updateQty(productId, 1);
            break;
          case 'dec':
            updateQty(productId, -1);
            break;
          case 'remove':
            removeItem(productId);
            break;
          case 'add':
          case 'restore':
            addItem(productId, price, name, leadTimeMinutes);
            break;
        }
      });
    }

    const cartContainer = document.getElementById('cart');
    if (cartContainer) {
      cartContainer.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        const action = target.dataset.action;
        const itemEl = target.closest('[data-product-id]');
        if (!itemEl) return;

        const productId = itemEl.dataset.productId;

        switch (action) {
          case 'inc':
            updateQty(productId, 1);
            break;
          case 'dec':
            updateQty(productId, -1);
            break;
        }
      });
    }

    // 2026-08-04: same inc/dec contract as #cart above, for the checkout
    // page's own order-item list (#orderItemsList) — see
    // renderOrderItemsList().
    const orderItemsList = document.getElementById('orderItemsList');
    if (orderItemsList) {
      orderItemsList.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        const action = target.dataset.action;
        const itemEl = target.closest('[data-product-id]');
        if (!itemEl) return;

        const productId = itemEl.dataset.productId;

        switch (action) {
          case 'inc':
            updateQty(productId, 1);
            break;
          case 'dec':
            updateQty(productId, -1);
            break;
        }
      });
    }

    const offcanvasEl = document.getElementById('offcanvasCart');
    if (offcanvasEl && typeof bootstrap !== 'undefined') {
      offcanvasInstance = bootstrap.Offcanvas.getOrCreateInstance(offcanvasEl);
    }

    const recentlyDeletedList = document.getElementById('recentlyDeletedList');
    if (recentlyDeletedList) {
      recentlyDeletedList.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        if (target.dataset.action !== 'restore') return;

        const itemEl = target.closest('[data-product-id]');
        if (!itemEl) return;

        const productId = itemEl.dataset.productId;
        const price = parseInt(itemEl.dataset.price, 10);
        const name = itemEl.dataset.name;
        const leadTimeMinutes = parseInt(itemEl.dataset.leadTimeMinutes || '0', 10);

        addItem(productId, price, name, leadTimeMinutes);
      });
    }
  }

  function setAddForLaterItems(items) {
    addForLaterItems = items || [];
    updateAllUI();
  }

  function getAddForLaterItems() {
    return addForLaterItems;
  }

  return {
    init: function () {
      setupEventListeners();
      updateAllUI();
      // setupCheckoutForm (defined later in this file, hoisted — global
      // function declarations resolve at call time regardless of source
      // order) attaches the #checkout-form submit listener that does
      // preventDefault + POST /api/orders + YooKassa widget/redirect.
      // It safely no-ops on any page without #checkout-form (cart.html,
      // menu.html). 2026-08-01 fix: this was previously only reachable via
      // initCheckoutPage(), itself never called from anywhere (dead code) —
      // the checkout submit button silently fell through to the browser's
      // native form submission (no method/action on #checkout-form → GET
      // reload of the current page) instead of ever calling /api/orders.
      setupCheckoutForm();
      // Same dead-code trap as setupCheckoutForm() above (2026-08-01 fix) —
      // wirePromoWidget() lived inside initCheckoutPage(), never called from
      // anywhere. No-ops on pages without #promo-apply-btn.
      wirePromoWidget();
    },
    addItem: addItem,
    updateQty: updateQty,
    setQty: setQty,
    removeItem: removeItem,
    clearCart: clearCart,
    undoClearCart: undoClearCart,
    getItemQty: getItemQty,
    getItems: getItems,
    getMaxLeadTime: getMaxLeadTime,
    loadCart: loadCart,
    getTotalPrice: getTotalPrice,
    setAppliedPromo: function (promo) { appliedPromo = promo; },
    updateAllUI: updateAllUI,
    updateNavbarCart: updateNavbarCart,
    updateProductControls: updateProductControls,
    renderCartPage: renderCartPage,
    updateCheckoutTotal: updateCheckoutTotal,
    renderRecentlyDeletedOnCheckout: renderRecentlyDeletedOnCheckout,
    renderRecentlyDeletedOnCart: renderRecentlyDeletedOnCart,
    showToast: showToast,
    showUpsellToast: showUpsellToast,
    setUpsellSuggestions: function (items) {
      upsellSuggestions.length = 0;
      upsellSuggestions.push(...items);
      updateAllUI();
    },
    setAddForLaterItems: setAddForLaterItems,
    getAddForLaterItems: getAddForLaterItems,
    getDeliveryFee: getDeliveryFee,
    QTY_MAX: QTY_MAX,
    MAX_ITEMS: MAX_ITEMS
  };
})();

function cartLoad() {
  return CartManager.loadCart();
}

function cartSave(items) {
  localStorage.setItem('cart', JSON.stringify(items));
}

function cartFind(items, productId) {
  return items.findIndex(x => x.product_id === productId);
}

function getTotalItems(items) {
  return items.reduce((sum, item) => sum + item.qty, 0);
}

function getTotalPrice(items) {
  return items.reduce((sum, item) => sum + (item.price_rub * item.qty), 0);
}

function cartSetQty(productId, priceRub, name, qty, leadTimeMinutes) {
  return CartManager.setQty(productId, priceRub, name, qty, leadTimeMinutes);
}

function cartInc(productId, priceRub, name, leadTimeMinutes) {
  return CartManager.addItem(productId, priceRub, name, leadTimeMinutes);
}

function cartDec(productId) {
  return CartManager.updateQty(productId, -1);
}

function cartUpdateQty(productId, priceRub, name, delta) {
  return CartManager.updateQty(productId, delta);
}

function cartGetItems() {
  return CartManager.getItems();
}

function cartClear() {
  return CartManager.clearCart();
}

function getQty(productId) {
  return CartManager.getItemQty(productId);
}

function updateQtyInput(productId) {
  CartManager.updateAllUI();
}

function updateAllQtyInputs() {
  CartManager.updateProductControls();
}

function updateCartBar() {
  CartManager.updateNavbarCart();
}

function updateCartBadge() {
  CartManager.updateNavbarCart();
}

function renderCart() {
  CartManager.renderCartPage();
}

function initCartPage() {
  CartManager.renderCartPage();
  CartManager.updateNavbarCart();
  CartManager.renderRecentlyDeletedOnCart();
}

function initCheckoutPage() {
  CartManager.renderCartPage();
  const _pu = document.getElementById('delivery_pickup');
  CartManager.updateCheckoutTotal(_pu && _pu.checked);
  CartManager.renderRecentlyDeletedOnCheckout();
  setupCheckoutForm();
}

function wirePromoWidget() {
  const input = document.getElementById('promo-code-input');
  const applyBtn = document.getElementById('promo-apply-btn');
  const resultEl = document.getElementById('promo-result');
  const hiddenCodeEl = document.getElementById('f-promo-code');
  if (!input || !applyBtn || !resultEl || !hiddenCodeEl) return;

  const showResult = (text, ok) => {
    resultEl.textContent = text;
    resultEl.classList.remove('d-none', 'text-success', 'text-danger');
    resultEl.classList.add(ok ? 'text-success' : 'text-danger');
  };

  const clearAppliedPromo = () => {
    CartManager.setAppliedPromo(null);
    hiddenCodeEl.value = '';
    // 2026-08-04 fix: was updateCheckoutTotal() only — left navbar/sticky
    // bar totals stuck on their pre-promo value. updateAllUI() also updates
    // updateNavbarCart()/updateStickyCartBar(), which now factor in
    // appliedPromo (see _widgetDeliveryFee / discountRub in those functions).
    CartManager.updateAllUI();
  };

  // Код в поле изменён после последней проверки — старая скидка больше не
  // валидна для нового текста, снимаем до повторного нажатия "Применить".
  input.addEventListener('input', clearAppliedPromo);

  applyBtn.addEventListener('click', async () => {
    const code = input.value.trim();
    if (!code) {
      showResult('Введите код', false);
      return;
    }

    const items = CartManager.loadCart();
    const subtotal = CartManager.getTotalPrice(items);

    applyBtn.disabled = true;
    const originalText = applyBtn.innerHTML;
    applyBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
      const response = await fetch('/api/promo/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, subtotal: subtotal }),
      });
      const data = await response.json();

      if (response.ok && data.valid) {
        CartManager.setAppliedPromo({
          code: code,
          discount_rub: data.discount_rub || 0,
          free_delivery: !!data.free_delivery,
        });
        hiddenCodeEl.value = code;
        showResult('Промокод применён', true);
      } else {
        clearAppliedPromo();
        showResult('Промокод не найден или недействителен', false);
      }
    } catch (error) {
      console.error('Promo check error:', error);
      clearAppliedPromo();
      showResult('Ошибка проверки промокода. Попробуйте позже.', false);
    } finally {
      applyBtn.disabled = false;
      applyBtn.innerHTML = originalText;
      // same fix as clearAppliedPromo above — navbar/sticky bar need this too
      await CartManager.updateAllUI();
    }
  });
}

function generateIdempotencyKey() {
  if (typeof window.crypto !== 'undefined' && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }

  if (typeof window.crypto !== 'undefined' && typeof window.crypto.getRandomValues === 'function') {
    const array = new Uint32Array(4);
    window.crypto.getRandomValues(array);
    const hex = Array.from(array, n => n.toString(16).padStart(8, '0')).join('');
    return hex.substring(0, 8) + '-' + hex.substring(8, 12) + '-4' + hex.substring(13, 16) + '-a' + hex.substring(17, 20) + '-' + hex.substring(20, 32);
  }

  console.error('Crypto API is not available - idempotency protection disabled');
  return 'fallback_' + Date.now() + '_' + Math.random().toString(36).substring(2, 11);
}

function validatePhone(phone) {
  const cleanPhone = phone.replace(/\D/g, '');
  return /^[78]\d{10}$/.test(cleanPhone);
}

function setupCheckoutForm() {
  const form = document.getElementById('checkout-form');
  if (!form) return;

  form.addEventListener('submit', async function (e) {
    e.preventDefault();

    const items = CartManager.loadCart();
    if (items.length === 0) {
      CartManager.showToast('Корзина пуста', 'warning');
      return;
    }

    const submitBtn = document.getElementById('checkout-submit');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Оформляем...';

const errorDiv = document.getElementById('checkout-error');
    if (errorDiv) {
      errorDiv.classList.add('d-none');
    }

    const sanitizeInput = (str) => {
      if (!str) return '';
      return String(str)
        .replace(/[<>{}\\\/]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
    };

    // Contract compliance: ссылаемся на f-* ID (синхронизируем с фактическими полями)
    const fNameEl = document.getElementById('f-name');
    const fPhoneEl = document.getElementById('f-phone');
    const fAddressEl = document.getElementById('f-address');
    const fCommentEl = document.getElementById('f-comment');
    const nameInput = document.getElementById('name');
    const phoneInput = document.getElementById('phone');
    const addressInput = document.getElementById('address');
    const commentInput = document.getElementById('comment');
    if (fNameEl && nameInput) fNameEl.value = nameInput.value;
    if (fPhoneEl && phoneInput) fPhoneEl.value = phoneInput.value;
    if (fAddressEl && addressInput) fAddressEl.value = addressInput.value;
    if (fCommentEl && commentInput) fCommentEl.value = commentInput.value;

    const name = sanitizeInput(nameInput ? nameInput.value : (fNameEl ? fNameEl.value : ''));
    const phone = sanitizeInput(phoneInput ? phoneInput.value : (fPhoneEl ? fPhoneEl.value : ''));
    const address = sanitizeInput(addressInput ? addressInput.value : (fAddressEl ? fAddressEl.value : ''));
    const comment = sanitizeInput(commentInput ? commentInput.value : (fCommentEl ? fCommentEl.value : '')) || null;

    if (name.length < 2) {
      CartManager.showToast('Введите корректное имя', 'warning');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
      return;
    }

    if (!validatePhone(phone)) {
      CartManager.showToast('Введите корректный номер телефона (+7XXXXXXXXXX)', 'warning');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
      return;
    }

    const deliveryModeInput = document.querySelector('input[name="delivery_mode"]:checked');
    const deliveryMode = deliveryModeInput ? deliveryModeInput.value : 'asap';
    const isPickup = deliveryMode === 'pickup';

    if (!isPickup && address.length < 8) {
      CartManager.showToast('Введите полный адрес доставки', 'warning');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
      return;
    }

    const zoneSelect = document.getElementById('f-zone');
    const zoneId = zoneSelect ? (zoneSelect.value || null) : null;

    if (!isPickup && !zoneId) {
      CartManager.showToast('Выберите зону доставки', 'warning');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
      return;
    }

    const paymentMethodInput = document.querySelector('input[name="payment_method"]:checked');
    if (!paymentMethodInput) {
      CartManager.showToast('Выберите способ оплаты', 'warning');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
      return;
    }

    const formData = {
      name: name,
      phone: phone,
      address: isPickup ? null : address,
      comment: comment,
      delivery_mode: deliveryMode,
      delivery_slot: null,
      delivery_date: null,
      payment_method: paymentMethodInput.value,
      zone_id: zoneId,
      items: items.map(item => ({
        product_id: item.product_id,
        qty: item.qty
      })),
      idempotency_key: generateIdempotencyKey(),
      promo_code: document.getElementById('f-promo-code')?.value || null
      // client_max_uid intentionally NOT set here (sprint_max_storefront):
      // the backend (app/api/routes/checkout.py) ignores whatever this
      // field carries and derives it itself from the X-Max-Init-Data
      // header below via validate_init_data() — a client-claimed int here
      // was never trustworthy (that was the whole point of the sprint).
    };

    // MAX Mini App: window.WebApp.initData is a raw, MAX-signed string
    // (https://dev.max.ru/docs/webapps/bridge) — present only when this
    // page is actually running inside the MAX client via the max-web-app.js
    // bridge (see shop_base.html). Absent everywhere else (plain browser),
    // in which case no header is sent and client_max_uid stays null
    // server-side, same as before this sprint.
    const maxInitData = (window.WebApp && window.WebApp.initData) || null;

    // Telegram Mini App: window.Telegram.WebApp.initData, same role as
    // maxInitData above (sprint_telegram_miniapp_frontend) — present only
    // inside Telegram's client via telegram-web-app.js (shop_base.html).
    // client_telegram_uid intentionally NOT set in formData for the same
    // reason as client_max_uid above — checkout.py derives it itself from
    // this header via validate_init_data(), never trusts a client value.
    const telegramInitData =
      (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || null;

    try {
      const response = await fetch('/api/orders', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          ...(maxInitData ? { 'X-Max-Init-Data': maxInitData } : {}),
          ...(telegramInitData ? { 'X-Telegram-Init-Data': telegramInitData } : {})
        },
        body: JSON.stringify(formData)
      });

      const data = await response.json();

      if (response.ok && data.ok) {
        localStorage.setItem('cart', '[]');

        if (data.confirmation_token) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = originalText;
          showYooKassaWidget(data.confirmation_token, data.order_id);
        } else if (data.confirmation_url) {
          // sprint_telegram_3ds_webview_redirect: server chose YooKassa's
          // "redirect" flow (Telegram WebView request) instead of
          // "embedded" — the 3DS challenge must NOT be rendered as an
          // iframe on this page (Telegram's WebView blocks the
          // third-party cookies the bank ACS challenge needs). Open the
          // system browser via the Telegram Bridge instead; return_url
          // brings the customer back to /thanks/{order_id} on success.
          submitBtn.disabled = false;
          submitBtn.innerHTML = originalText;
          if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openLink) {
            window.Telegram.WebApp.openLink(data.confirmation_url);
          } else {
            window.location.href = data.confirmation_url;
          }
        } else {
          window.location.href = `/thanks/${data.order_id}`;
        }
      } else {
        const errorMsg = data.detail || 'Ошибка при оформлении заказа';
        CartManager.showToast(errorMsg, 'error');

        if (errorDiv) {
          errorDiv.textContent = errorMsg;
          errorDiv.classList.remove('d-none');
        }

        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
      }
    } catch (error) {
      console.error('Error:', error);
      CartManager.showToast('Ошибка соединения. Попробуйте позже.', 'error');
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
    }
  });
}

function showError(message) {
  CartManager.showToast(message, 'error');
  const errorDiv = document.getElementById('checkout-error');
  if (errorDiv) {
    errorDiv.textContent = message;
    errorDiv.classList.remove('d-none');
  }
}

function showYooKassaWidget(confirmationToken, orderId) {
  function _renderWidget() {
    let container = document.getElementById('yookassa-widget');
    const overlayStyle = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
    if (!container) {
      container = document.createElement('div');
      container.id = 'yookassa-widget';
      document.body.appendChild(container);
    }
    // checkout.html provides this container pre-hidden (static markup) —
    // un-hide it and (re)apply the overlay styling regardless of whether it
    // was just created above or already existed.
    container.hidden = false;
    container.style.cssText = overlayStyle;

    let inner = document.getElementById('payment-form');
    if (!inner) {
      inner = document.createElement('div');
      inner.id = 'payment-form';
      inner.style.cssText = 'background:#fff;border-radius:12px;padding:24px;width:100%;max-width:480px;max-height:90vh;overflow:auto;';
      container.appendChild(inner);
    }
    inner.innerHTML = '';

    // sprint_yookassa_sbp_sberbank_only (2026-08-19): confirmed against
    // YooKassa's own docs (additional-settings/separate-payment-methods) —
    // the backend's payment_method_types (create_payment) does NOT filter
    // what the widget displays; that's a completely separate mechanism.
    // "По умолчанию на платёжной форме отображаются все способы оплаты,
    // которые доступны магазину и есть в виджете." Filtering the widget's
    // own picker requires customization.payment_methods PER INSTANCE, and
    // a single instance can't combine sbp+sberbank (only bank_card+mir_pay
    // is an allowed combination — anything else throws
    // invalid_combination_of_payment_methods). Two widget instances
    // sharing the same confirmation_token is the documented supported
    // pattern for showing more than one unrelated method on one page.
    const rails = [
      { method: 'sbp', label: 'СБП' },
      { method: 'sberbank', label: 'SberPay' },
    ];
    const widgets = [];
    let settled = false;

    function finishOnce(redirect) {
      if (settled) return;
      settled = true;
      widgets.forEach((w) => {
        try { w.destroy(); } catch (e) { /* already torn down by the widget itself */ }
      });
      document.getElementById('yookassa-widget')?.remove();
      if (redirect) window.location.href = `/thanks/${orderId}`;
    }

    function dropRail(section) {
      section.remove();
      if (!inner.querySelector('[data-yk-rail]')) {
        CartManager.showToast('Не удалось загрузить способы оплаты. Попробуйте позже.', 'error');
        finishOnce(false);
      }
    }

    rails.forEach(({ method, label }) => {
      const section = document.createElement('div');
      section.dataset.ykRail = method;
      section.style.cssText = 'margin-bottom:16px;';
      const heading = document.createElement('div');
      heading.className = 'small fw-semibold text-muted mb-2';
      heading.textContent = label;
      const mount = document.createElement('div');
      mount.id = `payment-form-${method}`;
      section.appendChild(heading);
      section.appendChild(mount);
      inner.appendChild(section);

      const checkout = new window.YooMoneyCheckoutWidget({
        confirmation_token: confirmationToken,
        return_url: `${window.location.origin}/thanks/${orderId}`,
        customization: { payment_methods: [method] },
        error_callback(error) {
          console.error(`YooKassa widget error (${method}):`, error);
          dropRail(section);
        }
      });
      widgets.push(checkout);

      checkout.render(mount.id).then(() => {
        checkout.on('success', () => finishOnce(true));
        checkout.on('fail', () => {
          CartManager.showToast(`Оплата через ${label} не прошла. Попробуйте другой способ.`, 'error');
        });
      }).catch((error) => {
        // e.g. no_payment_methods_to_display for a two-stage (capture:false)
        // payment — not our case (capture is always true), but fail closed
        // per-rail rather than breaking the whole overlay either way.
        console.error(`YooKassa widget render failed (${method}):`, error);
        dropRail(section);
      });
    });
  }

  if (window.YooMoneyCheckoutWidget) {
    _renderWidget();
  } else {
    const script = document.createElement('script');
    script.src = 'https://yookassa.ru/checkout-widget/v1/checkout-widget.js';
    script.onload = _renderWidget;
    script.onerror = () => CartManager.showToast('Не удалось загрузить платёжный виджет.', 'error');
    document.head.appendChild(script);
  }
}

document.addEventListener('DOMContentLoaded', function () {
  CartManager.init();
});