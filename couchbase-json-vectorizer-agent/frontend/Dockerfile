FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

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
