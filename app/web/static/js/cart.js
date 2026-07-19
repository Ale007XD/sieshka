/* app/web/static/js/cart.js — CartManager + checkout wiring (sprint_m7_frontend_scaffold)
 *
 * Real cart.js supersedes the older app.js entirely (never merged). Implements:
 *  - CartManager: localStorage-backed cart, qty controls, undo-delete history,
 *    toast queue, upsell suggestions.
 *  - Checkout: posts to POST /api/orders (NOT /checkout), sends zone_id and
 *    client_max_uid, renders the embedded YooKassa widget from confirmation_token.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "sieshka_cart_v1";
  var UID_PARAM = "max_uid";

  function uuidv4() {
    if (window.crypto && window.crypto.randomUUID) {
      try { return window.crypto.randomUUID(); } catch (e) { /* fall through */ }
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function parseMaxUid() {
    try {
      var params = new URLSearchParams(window.location.search);
      var raw = params.get(UID_PARAM);
      if (raw === null) return null;
      var n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : null;
    } catch (e) {
      return null;
    }
  }

  function formatRub(kopecks) {
    return (Math.round(Number(kopecks)) / 100).toLocaleString("ru-RU") + " ₽";
  }

  function CartManager() {
    this.items = [];
    this._undoStack = [];
    this._toastQueue = [];
    this._toastTimer = null;
    this.load();
  }

  CartManager.prototype.load = function () {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      this.items = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(this.items)) this.items = [];
    } catch (e) {
      this.items = [];
    }
    return this.items;
  };

  CartManager.prototype.save = function () {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this.items));
    } catch (e) { /* storage full / disabled — ignore */ }
  };

  CartManager.prototype.add = function (product) {
    var existing = this.items.find(function (it) { return it.product_id === product.product_id; });
    if (existing) {
      existing.qty += 1;
    } else {
      this.items.push({
        product_id: product.product_id,
        name: product.name,
        price_rub: product.price_rub,
        qty: 1,
      });
    }
    this.save();
    this.toast("Добавлено: " + product.name);
  };

  CartManager.prototype.setQty = function (productId, qty) {
    var it = this.items.find(function (x) { return x.product_id === productId; });
    if (!it) return;
    if (qty <= 0) { this.remove(productId); return; }
    it.qty = qty;
    this.save();
  };

  CartManager.prototype.remove = function (productId) {
    var idx = this.items.findIndex(function (x) { return x.product_id === productId; });
    if (idx === -1) return;
    var removed = this.items[idx];
    this._undoStack.push(JSON.parse(JSON.stringify(removed)));
    this.items.splice(idx, 1);
    this.save();
    this.toast("Удалено: " + removed.name, "undo");
  };

  CartManager.prototype.undo = function () {
    var item = this._undoStack.pop();
    if (!item) return;
    this.items.push(item);
    this.save();
    this.toast("Возвращено: " + item.name);
  };

  CartManager.prototype.clear = function () {
    this.items = [];
    this.save();
  };

  CartManager.prototype.goodsTotal = function () {
    return this.items.reduce(function (sum, it) {
      return sum + it.price_rub * it.qty;
    }, 0);
  };

  CartManager.prototype.count = function () {
    return this.items.reduce(function (n, it) { return n + it.qty; }, 0);
  };

  CartManager.prototype.isEmpty = function () {
    return this.items.length === 0;
  };

  CartManager.prototype.toCheckoutItems = function () {
    return this.items.map(function (it) {
      return { product_id: it.product_id, qty: it.qty };
    });
  };

  CartManager.prototype.suggestUpsell = function (catalog) {
    if (!Array.isArray(catalog) || catalog.length === 0) return null;
    var inCart = this.items.map(function (it) { return it.product_id; });
    var candidates = catalog.filter(function (p) { return inCart.indexOf(p.product_id) === -1; });
    if (candidates.length === 0) return null;
    return candidates[Math.floor(Math.random() * candidates.length)];
  };

  /* ---- toast queue ---- */
  CartManager.prototype.toast = function (message, kind) {
    this._toastQueue.push({ message: message, kind: kind || "info" });
    if (this._toastTimer === null) this._drainToasts();
  };

  CartManager.prototype._drainToasts = function () {
    var self = this;
    var next = this._toastQueue.shift();
    if (!next) { this._toastTimer = null; return; }
    var root = document.getElementById("toast-root");
    if (root) {
      var el = document.createElement("div");
      el.className = "toast";
      el.textContent = next.message;
      if (next.kind === "undo") {
        var btn = document.createElement("button");
        btn.textContent = "Отменить";
        btn.style.marginLeft = "0.5rem";
        btn.onclick = function () { self.undo(); self.renderCart(); };
        el.appendChild(btn);
      }
      root.appendChild(el);
      window.setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
        self._drainToasts();
      }, 2600);
    } else {
      this._drainToasts();
    }
  };

  /* ---- rendering ---- */
  CartManager.prototype.updateNavCount = function () {
    var badge = document.getElementById("cart-count");
    if (!badge) return;
    var n = this.count();
    if (n > 0) { badge.textContent = String(n); badge.hidden = false; }
    else { badge.hidden = true; }
  };

  CartManager.prototype.renderCart = function () {
    this.updateNavCount();
    var root = document.getElementById("cart-root");
    if (!root) return;
    if (this.isEmpty()) {
      root.innerHTML = '<p class="muted">Корзина пуста.</p>';
      var summary = document.getElementById("cart-summary");
      if (summary) summary.hidden = true;
      return;
    }
    var html = "";
    this.items.forEach(function (it) {
      html +=
        '<div class="cart-item" data-id="' + it.product_id + '">' +
        '<div class="ci-info"><div class="ci-name">' + escapeHtml(it.name) + "</div>" +
        '<span class="ci-qty"><button data-act="dec">−</button>' +
        "<span>" + it.qty + "</span>" +
        '<button data-act="inc">+</button></span></div>' +
        '<div class="ci-price">' + formatRub(it.price_rub * it.qty) + "</div>" +
        '<button class="ci-remove" data-act="rm">✕</button></div>';
    });
    root.innerHTML = html;
    root.querySelectorAll(".cart-item").forEach(function (row) {
      var id = row.getAttribute("data-id");
      row.querySelector('[data-act="inc"]').onclick = function () {
        var it = this.items.find(function (x) { return x.product_id === id; });
        if (it) this.setQty(id, it.qty + 1);
        this.renderCart();
      }.bind(this);
      row.querySelector('[data-act="dec"]').onclick = function () {
        var it = this.items.find(function (x) { return x.product_id === id; });
        if (it) this.setQty(id, it.qty - 1);
        this.renderCart();
      }.bind(this);
      row.querySelector('[data-act="rm"]').onclick = function () {
        this.remove(id);
        this.renderCart();
      }.bind(this);
    }.bind(this));

    var summary = document.getElementById("cart-summary");
    if (summary) {
      summary.hidden = false;
      document.getElementById("cart-goods").textContent = formatRub(this.goodsTotal());
      var deliveryEl = document.getElementById("cart-delivery");
      var totalEl = document.getElementById("cart-total");
      deliveryEl.textContent = "рассчитывается при оформлении";
      totalEl.textContent = formatRub(this.goodsTotal());
    }
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---- checkout form ---- */
  function setupCheckoutForm(cart) {
    var form = document.getElementById("checkout-form");
    if (!form) return;

    if (cart.isEmpty()) {
      var empty = document.getElementById("checkout-empty");
      if (empty) empty.hidden = false;
      form.hidden = true;
      return;
    }

    loadZones();

    var widgetMount = document.getElementById("yookassa-widget");
    var paymentRadios = form.querySelectorAll('input[name="payment_method"]');

    function toggleAddress() {
      var mode = form.querySelector('input[name="delivery_mode"]:checked').value;
      var addressField = document.getElementById("address-field");
      var zoneField = document.getElementById("f-zone").closest(".field");
      if (mode === "pickup") {
        addressField.hidden = true;
        if (zoneField) zoneField.hidden = true;
      } else {
        addressField.hidden = false;
        if (zoneField) zoneField.hidden = false;
      }
    }
    form.querySelectorAll('input[name="delivery_mode"]').forEach(function (r) {
      r.addEventListener("change", toggleAddress);
    });
    toggleAddress();

    function syncSubmitLabel() {
      var method = form.querySelector('input[name="payment_method"]:checked').value;
      var btn = document.getElementById("checkout-submit");
      btn.textContent = method === "cash" ? "Заказать (наличные)" : "Оплатить и заказать";
    }
    paymentRadios.forEach(function (r) { r.addEventListener("change", syncSubmitLabel); });
    syncSubmitLabel();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var errEl = document.getElementById("checkout-error");
      errEl.hidden = true;

      var deliveryMode = form.querySelector('input[name="delivery_mode"]:checked').value;
      var paymentMethod = form.querySelector('input[name="payment_method"]:checked').value;
      var name = form.querySelector("#f-name").value.trim();
      var phone = form.querySelector("#f-phone").value.trim();
      var address = form.querySelector("#f-address").value.trim() || null;
      var zoneRaw = form.querySelector("#f-zone").value;
      // BUGFIX (2026-07-19): was parseInt(zoneRaw, 10) — delivery_zones.id is
      // a UUID (see loadZones() below: opt.value = String(z.id)), never an
      // integer. parseInt() on a UUID string produces NaN (JSON.stringify
      // then silently turns that into null), so the selected zone was never
      // actually saved — only worked by coincidence when the API's
      // zone_id field was still typed as int on the backend. Pass the UUID
      // string straight through.
      var zoneId = zoneRaw === "" ? null : zoneRaw;
      var comment = form.querySelector("#f-comment").value.trim() || null;

      if (!name || !phone) {
        return showError(errEl, "Укажите имя и телефон.");
      }
      if (deliveryMode !== "pickup" && zoneId === null) {
        return showError(errEl, "Выберите зону доставки.");
      }

      var body = {
        name: name,
        phone: phone,
        address: deliveryMode === "pickup" ? null : address,
        comment: comment,
        delivery_mode: deliveryMode,
        delivery_slot: null,
        delivery_date: null,
        payment_method: paymentMethod,
        zone_id: zoneId,
        items: cart.toCheckoutItems(),
        idempotency_key: uuidv4(),
        client_max_uid: parseMaxUid(),
      };

      var submitBtn = document.getElementById("checkout-submit");
      submitBtn.disabled = true;

      fetch("/api/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) {
            throw new Error(res.data.detail || "Ошибка оформления заказа");
          }
          var data = res.data;
          if (data.confirmation_token) {
            showYooKassaWidget(widgetMount, data.confirmation_token, data.order_id, cart);
          } else {
            cart.clear();
            window.location.href = "/thanks/" + data.order_id;
          }
        })
        .catch(function (err) {
          submitBtn.disabled = false;
          showError(errEl, err.message || "Не удалось оформить заказ.");
        });
    });
  }

  function showError(el, msg) {
    el.textContent = msg;
    el.hidden = false;
  }

  function showYooKassaWidget(mount, token, orderId, cart) {
    if (!mount) { window.location.href = "/thanks/" + orderId; return; }
    mount.hidden = false;
    mount.innerHTML = "";
    var script = document.createElement("script");
    script.src = "https://yookassa.ru/checkout-widget/v1/checkout-widget.js";
    script.onload = function () {
      if (!window.YooMoneyCheckoutWidget) return;
      var widget = new window.YooMoneyCheckoutWidget({
        confirmation_token: token,
        return_url: window.location.origin + "/thanks/" + orderId,
        embedded_3ds: true,
      });
      widget.on("success", function () {
        cart.clear();
        window.location.href = "/thanks/" + orderId;
      });
      widget.on("error", function (err) {
        mount.innerHTML = '<div class="notice notice-error">Ошибка оплаты: ' +
          escapeHtml(err && err.message ? err.message : "неизвестная ошибка") + "</div>";
      });
      widget.render(mount);
    };
    script.onerror = function () {
      mount.innerHTML = '<div class="notice notice-error">Не удалось загрузить виджет оплаты.</div>';
    };
    document.head.appendChild(script);
  }

  function loadZones() {
    var select = document.getElementById("f-zone");
    if (!select) return;
    fetch("/api/delivery-zones")
      .then(function (r) { return r.json(); })
      .then(function (zones) {
        zones.forEach(function (z) {
          var opt = document.createElement("option");
          opt.value = String(z.id);
          opt.textContent = z.name + (z.delivery_time_minutes ? " (" + z.delivery_time_minutes + " мин)" : "");
          select.appendChild(opt);
        });
      })
      .catch(function () { /* zones optional at render time */ });
  }

  window.CartManager = CartManager;
  window.sieshkaCart = new CartManager();

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("cart-root")) {
      window.sieshkaCart.renderCart();
    }
    if (document.getElementById("checkout-form")) {
      setupCheckoutForm(window.sieshkaCart);
    }
  });
})();
