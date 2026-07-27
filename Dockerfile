FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl openssl \
    && rm -rf /var/lib/apt/lists/*

# GigaChat (Sber) uses a TLS chain signed by the Russian Ministry of Digital
# Development root CA, which is not in the standard ca-certificates bundle.
# litellm's native gigachat/ provider only disables SSL verification for its
# OAuth token exchange (authenticator.py) and file uploads (file_handler.py) —
# NOT for the actual /chat/completions call, so without this the real chat
# request fails with SSLCertVerificationError even with a valid API key.
RUN mkdir -p /usr/local/share/ca-certificates/russian_trusted \
    && curl -fsSL -o /usr/local/share/ca-certificates/russian_trusted/russian_trusted_root_ca.crt \
       https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
    && curl -fsSL -o /usr/local/share/ca-certificates/russian_trusted/russian_trusted_sub_ca.crt \
       https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
    && update-ca-certificates

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir --break-system-packages -e ".[nano]"

# httpx (used internally by litellm) verifies TLS against certifi's OWN
# cacert.pem bundle by default — NOT the system /etc/ssl/certs store that
# update-ca-certificates manages. Adding the Russian root CA to the OS trust
# store alone (see earlier RUN block) fixes curl/system tools, but litellm's
# actual GigaChat /chat/completions call still fails with the same
# SSLCertVerificationError until certifi's bundle itself is patched.
# MUST run AFTER pip install (needs certifi already installed + resolvable),
# and should be the LAST cert-related step so a later pip reinstall of
# certifi doesn't silently wipe this out.
#
# INCIDENT (2026-07-27): an earlier version of this step used plain
# `curl ... >> "$CERTIFI_CACERT"` with no validation. One of the two fetches
# silently returned non-PEM content (curl's `-f` only catches HTTP 4xx/5xx,
# not a 200 OK with the wrong body), corrupting cacert.pem as a whole —
# litellm creates an SSL context from this file eagerly at import time
# (litellm/fine_tuning/main.py), so the corrupted bundle crashed the entire
# app on startup (ssl.SSLError: [X509] PEM lib), not just the GigaChat path.
# Each fetched file is now validated with `openssl x509 -noout` BEFORE being
# appended — an invalid/non-PEM response fails the build instead of
# silently shipping a broken image.
RUN CERTIFI_CACERT=$(python3 -c "import certifi; print(certifi.where())") \
    && curl -fsSL -o /tmp/russian_root.crt \
       https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
    && curl -fsSL -o /tmp/russian_sub.crt \
       https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
    && openssl x509 -in /tmp/russian_root.crt -noout -text > /dev/null \
    && openssl x509 -in /tmp/russian_sub.crt -noout -text > /dev/null \
    && cat /tmp/russian_root.crt /tmp/russian_sub.crt >> "$CERTIFI_CACERT" \
    && rm /tmp/russian_root.crt /tmp/russian_sub.crt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]