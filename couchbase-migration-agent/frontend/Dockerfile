FROM node:20-slim AS build
WORKDIR /app

# --- Corporate TLS inspection support -------------------------------------
# If npm install fails here with a self-signed-certificate error, your
# network (Zscaler, Netskope, a corporate VPN, etc.) is intercepting HTTPS to
# the npm registry and this container doesn't trust its root CA. Export that
# CA from macOS Keychain Access and overwrite certs/corporate-ca.crt (repo
# root) with it -- see README for the exact steps. Left empty (the default),
# this is a no-op and update-ca-certificates just skips it. `certs` here is
# the additional build context wired up in docker-compose.yml, since this
# service's own build context is ./frontend, not the repo root.
COPY --from=certs corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

# Vite inlines import.meta.env.VITE_* into the built JS at `npm run build` time --
# it does NOT read them from the container's environment at runtime (there is no
# server-side process for a static build to read them from). So these have to
# arrive as build ARGs (see docker-compose.yml's `build.args` for this service),
# not as a runtime `environment:` block -- a runtime-only env var here is silently
# ignored by the already-built bundle. Rebuild (`docker compose up --build`) any
# time you change these.
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_WS_BASE_URL=ws://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_WS_BASE_URL=${VITE_WS_BASE_URL}
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY --from=certs corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 5173
CMD ["serve", "-s", "dist", "-l", "5173"]
