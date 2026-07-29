FROM python:3.11-slim

WORKDIR /app

# Trust an optional corporate TLS-inspection root CA (e.g. Netskope) before any
# network calls below -- see scripts/setup-corporate-ca.sh. No-op if
# certs/corporate-ca.crt doesn't exist (i.e. you're not behind Netskope).
COPY --from=corporate-ca . /tmp/corporate-ca/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && \
    if [ -s /tmp/corporate-ca/corporate-ca.crt ]; then \
      cp /tmp/corporate-ca/corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt && \
      update-ca-certificates; \
    fi && \
    rm -rf /tmp/corporate-ca
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
