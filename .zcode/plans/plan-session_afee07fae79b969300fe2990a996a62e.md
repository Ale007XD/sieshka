Фаза 2 — минимальные точечные патчи Sieshka (по утверждённому отчёту Фазы 1), строго по одному, с прогоном тестов после каждого:

P1 [CRITICAL, VULN-01+04] app/main.py: добавить dependencies=[Depends(get_current_username)] к include_router для orders_router, kitchen_router, delivery_router (те же гейты, что у admin_router). Публичный контракт витрины /api/* не трогаем. Обновить интеграционные тесты (Basic-заголовок). Проверка: pytest; curl → 401 без кредов.

P2 [CRITICAL, VULN-02] app/services/payment_service.py + app/webhooks/yookassa.py: перед confirm_payment перечитывать платёж через YooKassa API (GET /v3/payments/{id}, Basic shop auth), сверять status=="succeeded" и сумму/валюту с БД; при недоступности API — 500 (YooKassa ретраит; отступление от ADR-003 зафиксировать комментарием). Тесты с моком.

P3 [CRITICAL, VULN-03] app/api/routes/orders.py pay_order: amount только из order.total_rub (409 при несовпадении), return_url строить от settings.YOOKASSA_RETURN_URL, клиентское поле игнорировать.

P4 [HIGH, VULN-05] app/webhooks/max.py:101-104 и app/webhooks/telegram.py:73: пустой секрет → 403 (fail-closed) + hmac.compare_digest. Обновить тесты.

P5 [MEDIUM, VULN-06] CSRF-гвард на мутирующих /admin/ui/*: при наличии Origin — сверять host, чужой → 403; без Origin — пропуск (не ломает HTMX/curl/тесты).

P6 [MEDIUM, VULN-08] app/main.py: docs_url/redoc_url/openapi_url = None при не-DEBUG.

P7 [MEDIUM, VULN-07] thanks.html: замаскировать телефон, имя оставить.

P8 [MEDIUM, VULN-09] max_webapp_auth.py: отсутствие/просроченность auth_date → провал валидации (окно 24ч, env-override).

P9 [LOW, опционально] compare_digest в zalo_events.py:79 и web/auth.py:29; CSV-гвард «= + - @» в inventory_service.py; escapeHtml в menu.js:386; uv lock.

После каждого патча: pytest tests/unit (740 базовых) + затронутые интеграционные, mypy app/, ruff check . Не переходим к следующему при упавшем прогоне. Операционно (вне кода, сообщить отдельно): ротация OpenRouter-ключа и Telegram-токена из .env.

Не делаем: рефакторинг, обновление несвязанных зависимостей, reverse proxy/rate-limit (архитектурно, отдельное решение).