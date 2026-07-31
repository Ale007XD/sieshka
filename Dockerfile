FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl openssl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

RUN pip install --no-cache-dir --break-system-packages -e ".[nano]"

# GigaChat (Sber) uses a TLS chain signed by the Russian Ministry of Digital
# Development root CA. litellm's native gigachat/ provider only disables SSL
# verification for its OAuth token exchange and file uploads — NOT for the
# actual /chat/completions call, so this fails with SSLCertVerificationError
# even with a valid API key unless the root CA is trusted.
#
# INCIDENT (2026-07-27): an earlier attempt appended these certs directly to
# certifi's own cacert.pem with a plain `cat ... >> "$CERTIFI_CACERT"`.
# gu-st.ru serves its .crt files WITHOUT a trailing newline after
# "-----END CERTIFICATE-----" — `openssl x509 -noout` validates each file
# fine in isolation (it tolerates a missing trailing newline for a single
# certificate), but concatenating two such files with no separator merges
# "-----END CERTIFICATE----------BEGIN CERTIFICATE-----" onto one line.
# ssl.create_default_context() — the exact call litellm makes at import
# time (litellm/fine_tuning/main.py, eagerly, for every request) — is
# stricter about PEM block boundaries than openssl x509's single-cert
# parser, and fails the ENTIRE bundle with `ssl.SSLError: [X509] PEM lib`,
# crashing the whole app at startup (not just the GigaChat code path).
# Reproduced and confirmed locally before this fix; `echo` after each `cat`
# guarantees a newline separator regardless of the source file's own
# trailing-newline state.
#
# Written to a SEPARATE file (not certifi's own cacert.pem) and pointed to
# via SSL_CERT_FILE — litellm's get_ssl_configuration() checks this env var
# before falling back to certifi.where() (litellm/llms/custom_httpx/
# http_handler.py). This keeps certifi's original bundle untouched, so a
# bad rebuild here can be rolled back by just unsetting SSL_CERT_FILE,
# without needing to reinstall/repair the certifi package itself.
RUN CERTIFI_CACERT=$(python3 -c "import certifi; print(certifi.where())") \
    && mkdir -p /etc/ssl/custom \
    && cp "$CERTIFI_CACERT" /etc/ssl/custom/combined_ca_bundle.pem \
    && curl -fsSL -o /tmp/russian_root.crt \
       https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
    && curl -fsSL -o /tmp/russian_sub.crt \
       https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
    && openssl x509 -in /tmp/russian_root.crt -noout -text > /dev/null \
    && openssl x509 -in /tmp/russian_sub.crt -noout -text > /dev/null \
    && { cat /tmp/russian_root.crt; echo; cat /tmp/russian_sub.crt; echo; } \
       >> /etc/ssl/custom/combined_ca_bundle.pem \
    && rm /tmp/russian_root.crt /tmp/russian_sub.crt \
    && python3 -c "import ssl; ssl.create_default_context(cafile='/etc/ssl/custom/combined_ca_bundle.pem')"

ENV SSL_CERT_FILE=/etc/ssl/custom/combined_ca_bundle.pem

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]