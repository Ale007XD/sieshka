# Manual storefront smoke-test checklist — sprint_m7_static_assets_smoke

Browser-automation tooling (Playwright / Selenium) is **NOT** present in this
repo: `pyproject.toml` has no `playwright`/`selenium` dependency, there is no
`package.json`, and `node_modules` does not exist. Per the sprint's explicit
fallback ("at minimum a manual checklist if no browser-automation tooling
exists in this repo yet"), this document is the manual equivalent of the
browser-level smoke test. The automated equivalent is
`tests/integration/test_storefront_smoke.py` (full pipeline, Docker-gated) plus
`tests/unit/test_template_js_id_contract.py` (DOM-ID contract).

Run against the local dev stack (`docker compose up`, app on `:8000`):

## 1. Browse categories (menu loads client-side)
- [ ] `GET /` returns 200 and renders the storefront shell (`#menu-root`,
      `#menu-status` present).
- [ ] Menu data loads without console errors: `GET /api/menu?method=delivery`
      returns the real 89-product / 24-category data and `#menu-root` populates
      with category sections + product rows (each with a `+` add button).
- [ ] `#cart-count` badge in the header is hidden when the cart is empty.

## 2. Add to cart
- [ ] Clicking a product's `+` button fires `CartManager.add`, shows a toast
      ("Добавлено: …") and increments `#cart-count`.
- [ ] `GET /cart` renders `#cart-root` with the item, qty controls (`+`/`-`),
      a remove (`✕`) button, and `#cart-summary` showing goods total.
- [ ] Removing an item shows an "Отменить" (undo) toast; undo restores it.

## 3. Checkout — cash
- [ ] `GET /checkout` renders `#checkout-form` with name/phone/delivery_mode/
      payment_method fields, `#f-zone` (populated from `GET /api/delivery-zones`),
      and `#yookassa-widget` (hidden).
- [ ] Select **Наличные** (cash); submit button reads "Заказать (наличные)".
- [ ] Submit → `POST /api/orders` (payment_method=cash) returns
      `{ok:true, order_id}` with NO `confirmation_token`.
- [ ] Browser redirects to `/thanks/{order_id}`; the order exists and its state
      advanced DRAFT → CONFIRMED via the real nano-vm Program pipeline.

## 4. Checkout — yookassa_card
- [ ] Select **Карта (ЮKassa)**; submit button reads "Оплатить и заказать".
- [ ] Submit → `POST /api/orders` (payment_method=yookassa_card) returns
      `{ok:true, order_id, confirmation_token}`.
- [ ] The embedded YooKassa widget mounts in `#yookassa-widget` (the CSP allows
      `https://yookassa.ru`; the widget script loads and renders).
- [ ] On widget success the order state advanced
      DRAFT → CONFIRMED → PAYMENT_PENDING and the browser redirects to
      `/thanks/{order_id}`; cart is cleared.

## 5. DOM-ID contract (fixed: templates must match the JS, not vice-versa)
All ids referenced by `cart.js` / `menu.js` exist in the ported templates:
`#menu-root`, `#menu-status`, `#cart-root`, `#cart-summary`, `#cart-goods`,
`#cart-delivery`, `#cart-total`, `#cart-count`, `#toast-root`,
`#checkout-form`, `#checkout-empty`, `#checkout-submit`, `#checkout-error`,
`#yookassa-widget`, `#address-field`, `#f-zone`, `#f-name`, `#f-phone`,
`#f-address`, `#f-comment`. (Enforced automatically by
`test_template_js_id_contract.py`.)
