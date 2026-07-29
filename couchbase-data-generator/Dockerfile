# Couchbase Customer 360 Data Generator
FROM node:20-bookworm-slim

# The official Couchbase Node.js SDK ships native bindings and needs
# build tools + libssl on Debian-based images to install cleanly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    make \
    g++ \
    ca-certificates \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY server.js ./
COPY lib ./lib
COPY public ./public

ENV PORT=4300
EXPOSE 4300

CMD ["node", "server.js"]
