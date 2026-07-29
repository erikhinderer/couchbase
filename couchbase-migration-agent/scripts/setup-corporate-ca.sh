#!/usr/bin/env bash
# Exports the root CA(s) this Mac trusts for TLS-inspecting corporate proxies
# (Zscaler, Netskope, Palo Alto GlobalProtect, etc. -- usually pushed via MDM)
# so the Docker build can trust them too.
#
# Why this is needed: Docker Desktop's own image pulls go through macOS's
# system trust store (via the keychain), which is why `docker compose build`
# can fetch base images fine on a corporate laptop. But `pip install` and
# `npm install` *inside* the build run against each language's own bundled CA
# store, which has never heard of your corporate proxy's root cert. Once that
# proxy intercepts pip/npm's HTTPS traffic to PyPI/npm and re-signs it with
# its own cert, verification fails with something like:
#   SSLCertVerificationError: self-signed certificate in certificate chain
#
# This script exports the certs and drops them into certs/ (repo root),
# backend/certs/, and frontend/certs/ -- one per Docker build context, since
# each of those services builds from its own directory and COPY can't reach
# outside it. The corresponding Dockerfiles pick these up automatically and
# add them to the container's trust store before installing dependencies.
# Machines that don't need this (no corporate proxy) just get empty certs/
# directories, which the Dockerfiles treat as a no-op.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIRS=("$REPO_ROOT/certs" "$REPO_ROOT/backend/certs" "$REPO_ROOT/frontend/certs")

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script currently only supports macOS (it uses the 'security' CLI to read the keychain)." >&2
  echo "On Linux, ask IT for your org's proxy root CA .pem and copy it manually to:" >&2
  for d in "${DEST_DIRS[@]}"; do echo "  ${d#$REPO_ROOT/}/corporate-ca.crt" >&2; done
  exit 1
fi

TMP_CERT="$(mktemp)"
trap 'rm -f "$TMP_CERT"' EXIT

echo "Exporting certificates from the System keychain..."
# The System keychain (distinct from "System Roots", which is Apple's own
# bundle) is where MDM profiles install custom trusted root CAs -- including
# the SSL-inspection cert a corporate proxy uses to re-sign intercepted
# traffic. Dumping the whole keychain is harmless overkill: any extra certs
# just make the container's trust store a superset of what it strictly needs.
security find-certificate -a -p /Library/Keychains/System.keychain > "$TMP_CERT" 2>/dev/null || true

CERT_COUNT=$(grep -c "BEGIN CERTIFICATE" "$TMP_CERT" 2>/dev/null || echo 0)

if [[ "$CERT_COUNT" -eq 0 ]]; then
  echo "No certificates found in the System keychain -- nothing to export."
  echo "If your build still fails with a self-signed-certificate error, ask IT for"
  echo "the proxy's root CA and save it as corporate-ca.crt in each of:"
  for d in "${DEST_DIRS[@]}"; do echo "  ${d#$REPO_ROOT/}/"; done
  for d in "${DEST_DIRS[@]}"; do
    mkdir -p "$d"
    : > "$d/corporate-ca.crt"
  done
  exit 0
fi

for d in "${DEST_DIRS[@]}"; do
  mkdir -p "$d"
  cp "$TMP_CERT" "$d/corporate-ca.crt"
done

echo "Exported $CERT_COUNT certificate(s) to:"
for d in "${DEST_DIRS[@]}"; do echo "  ${d#$REPO_ROOT/}/corporate-ca.crt"; done
echo
echo "Now run: docker compose build --no-cache"
