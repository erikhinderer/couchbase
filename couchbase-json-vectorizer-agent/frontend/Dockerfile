FROM node:20-slim AS build
WORKDIR /app

# Trust an optional corporate TLS-inspection root CA (e.g. Netskope) before
# `npm install` hits the registry -- see scripts/setup-corporate-ca.sh. No-op
# if certs/corporate-ca.crt is empty (i.e. you're not behind Netskope).
# node:20-slim doesn't ship the ca-certificates package (no
# /usr/local/share/ca-certificates dir or update-ca-certificates binary), so
# install it first -- plain apt-get over HTTP, no cert trust needed for that.
COPY --from=corporate-ca . /tmp/corporate-ca/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && \
    if [ -s /tmp/corporate-ca/corporate-ca.crt ]; then \
      cp /tmp/corporate-ca/corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt && \
      update-ca-certificates; \
    fi && \
    rm -rf /tmp/corporate-ca
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

# Trust the same optional corporate CA before apk needs to fetch anything --
# appended directly to the OS bundle (rather than `apk add ca-certificates` +
# update-ca-certificates) because apk itself needs the trust in place before
# it can fetch any package, including ca-certificates.
COPY --from=corporate-ca . /tmp/corporate-ca/
RUN if [ -s /tmp/corporate-ca/corporate-ca.crt ]; then \
      cat /tmp/corporate-ca/corporate-ca.crt >> /etc/ssl/certs/ca-certificates.crt; \
    fi && \
    rm -rf /tmp/corporate-ca

# Self-signed cert generated at build time so the container works out of the
# box on `docker compose up` with no manual setup. For anything beyond local
# evaluation, replace /etc/nginx/ssl/cert.pem and key.pem with a real
# certificate -- e.g. mount your own files over this path as a volume (see
# docker-compose.yml's commented-out `ssl_certs` volume for the pattern), or
# point a reverse proxy/load balancer with a real cert at this container
# instead of exposing 443 directly.
RUN apk add --no-cache openssl && \
    mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout /etc/nginx/ssl/key.pem \
      -out /etc/nginx/ssl/cert.pem \
      -subj "/C=US/ST=NA/L=NA/O=Couchbase JSON Vectorizer Agent/CN=localhost"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 443 80
CMD ["nginx", "-g", "daemon off;"]
