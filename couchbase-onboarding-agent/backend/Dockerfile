FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements.txt .

# Trust an optional corporate/proxy root CA before installing anything from PyPI --
# see scripts/setup-corporate-ca.sh at the repo root. On a corporate laptop behind
# an SSL-inspecting proxy (Zscaler, Netskope, etc.), pip verifies against its own
# bundled CA store, not the OS trust store Docker Desktop's own image pulls rely
# on, so intercepted HTTPS traffic to PyPI fails with CERTIFICATE_VERIFY_FAILED
# unless that proxy's root cert is trusted here too. A no-op on machines that
# don't need it (certs/ stays empty).
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN if find /usr/local/share/ca-certificates/extra -name '*.crt' 2>/dev/null | grep -q .; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      update-ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# The Cassandra driver's optional C extension (cython-based murmur3/libev) needs a
# compiler toolchain to build from source on platforms without a prebuilt wheel;
# the driver works fine (just slightly slower token-hashing) without it, but build
# tools are kept here so `pip install` never fails outright for lack of them.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
