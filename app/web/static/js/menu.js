/* app/web/static/js/menu.js — client-side menu loader (sprint_m7_frontend_scaffold)
 *
 * Fetches /api/menu?method=<delivery|pickup> after page load and renders
 * categories/products into #menu-root. No server-injected menu context — the
 * server route for "/" / "/menu" only serves the shell.
 */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function formatRub(kopecks) {
    return (Math.round(Number(kopecks)) / 100).toLocaleString("ru-RU") + " ₽";
  }

  function deliveryMethod() {
    try {
      var params = new URLSearchParams(window.location.search);
      var m = params.get("method");
      return m === "pickup" ? "pickup" : "delivery";
    } catch (e) {
      return "delivery";
    }
  }

  function renderMenu(products) {
    var root = document.getElementById("menu-root");
    if (!root) return;
    var status = document.getElementById("menu-status");
    if (status) status.hidden = true;

    if (!Array.isArray(products) || products.length === 0) {
      root.innerHTML = '<p class="muted">Меню пока пусто.</p>';
      return;
    }

    var byCategory = {};
    products.forEach(function (p) {
      var cat = p.category || "Прочее";
      (byCategory[cat] = byCategory[cat] || []).push(p);
    });

    var html = "";
    Object.keys(byCategory).forEach(function (cat) {
      html += '<div class="menu-category"><h2>' + escapeHtml(cat) + "</h2>";
      byCategory[cat].forEach(function (p) {
        html +=
          '<div class="menu-item" data-id="' + p.product_id + '">' +
          '<div class="mi-info"><div class="mi-name">' + escapeHtml(p.name) + "</div>" +
          (p.description ? '<div class="mi-desc">' + escapeHtml(p.description) + "</div>" : "") +
          "</div>" +
          '<div class="mi-price">' + formatRub(p.price_rub) + "</div>" +
          '<button class="mi-add" data-id="' + p.product_id + '">+</button></div>';
      });
      html += "</div>";
    });
    root.innerHTML = html;

    root.querySelectorAll(".mi-add").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!window.sieshkaCart) return;
        var prod = products.find(function (p) { return p.product_id === btn.getAttribute("data-id"); });
        if (prod) {
          window.sieshkaCart.add({
            product_id: prod.product_id,
            name: prod.name,
            price_rub: prod.price_rub,
          });
          window.sieshkaCart.updateNavCount();
        }
      });
    });
  }

  function loadMenu() {
    var status = document.getElementById("menu-status");
    fetch("/api/menu?method=" + deliveryMethod())
      .then(function (r) {
        if (!r.ok) throw new Error("menu load failed: " + r.status);
        return r.json();
      })
      .then(function (data) {
        var products = Array.isArray(data) ? data : (data.products || []);
        renderMenu(products);
      })
      .catch(function (err) {
        if (status) {
          status.textContent = "Не удалось загрузить меню. Попробуйте обновить страницу.";
          status.hidden = false;
        }
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("menu-root")) {
      loadMenu();
    }
  });
})();
