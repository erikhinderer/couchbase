FROM node:20-slim AS build
WORKDIR /app
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

# Trust an optional corporate/proxy root CA before installing from npm -- see
# backend/Dockerfile for why this is needed (same SSL-inspecting-proxy issue,
# npm/Node side). Exported by scripts/setup-corporate-ca.sh into
# frontend/certs/; a no-op if that hasn't been run / isn't needed.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN if find /usr/local/share/ca-certificates/extra -name '*.crt' 2>/dev/null | grep -q .; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      update-ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

RUN npm install
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN if find /usr/local/share/ca-certificates/extra -name '*.crt' 2>/dev/null | grep -q .; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      update-ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 5173
CMD ["serve", "-s", "dist", "-l", "5173"]
