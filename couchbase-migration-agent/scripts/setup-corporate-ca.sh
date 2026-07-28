#!/usr/bin/env bash
# Couchbase laptops run Netskope, which does TLS inspection -- it swaps in its
# own cert for HTTPS traffic, including pypi.org and the npm registry. Without
# that cert trusted inside the Docker build containers, `docker compose up
# --build` fails with:
#   SSLError(SSLCertVerificationError(... 'self-signed certificate in
#   certificate chain' ...))
#
# This script finds Netskope's root CA in your Mac's keychain (it's pushed
# there by Couchbase IT/MDM already -- that's what lets Netskope intercept
# your traffic in the first place) and drops it in certs/corporate-ca.crt,
# where the Dockerfiles pick it up automatically. Safe to re-run.
#
# Not on a Couchbase-managed Mac, or this doesn't find anything? See the
# "Corporate network / TLS inspection" section in the README for manual
# steps (Keychain Access, or your platform's cert store).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$REPO_ROOT/certs/corporate-ca.crt"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only knows how to search the macOS keychain."
  echo "On Linux/Windows, export your org's TLS-inspection root CA yourself"
  echo "and save it (PEM format) to: $OUT"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Searching your keychains for a Netskope-issued root CA..."

FOUND=""
for KEYCHAIN in "/Library/Keychains/System.keychain" "$HOME/Library/Keychains/login.keychain-db"; do
  [[ -f "$KEYCHAIN" ]] || continue
  rm -f "$TMP"/cert-*.pem

  # Dump every cert in this keychain as one concatenated PEM stream, then
  # split it into individual cert files with awk so each can be inspected
  # separately. (Deliberately not using csplit: macOS ships the BSD variant,
  # which doesn't support GNU csplit's -z/-b flags -- it silently produced
  # zero files here, which is why the first version of this script always
  # reported "not found" even when the cert was right there in the keychain.)
  security find-certificate -a -p "$KEYCHAIN" 2>/dev/null | awk -v dir="$TMP" '
    /-----BEGIN CERTIFICATE-----/ { n++; file = dir "/cert-" n ".pem" }
    file { print > file }
  '

  for cert in "$TMP"/cert-*.pem; do
    [[ -f "$cert" ]] || continue
    # Netskope's Couchbase-tenant cert has a subject/issuer like
    # "CN=ns-swg.<region>.couchbase.goskope.com" -- goskope.com is Netskope's
    # own domain, so this matches regardless of region or Netskope's product
    # branding in the cert fields.
    if openssl x509 -noout -subject -in "$cert" 2>/dev/null | grep -qi "goskope"; then
      FOUND="$cert"
      break 2
    fi
  done
done

if [[ -z "$FOUND" ]]; then
  echo
  echo "Couldn't find a Netskope root CA automatically."
  echo "If you're not behind Netskope, you don't need this -- 'docker compose"
  echo "up --build' should just work. If you are, see the README's"
  echo "'Corporate network / TLS inspection' section for how to export it by hand."
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
cp "$FOUND" "$OUT"
echo "Found it. Wrote Netskope root CA to certs/corporate-ca.crt."

# Keep this local cert out of git history -- it's specific to your machine's
# Netskope deployment and shouldn't be committed.
cd "$REPO_ROOT"
git update-index --skip-worktree certs/corporate-ca.crt 2>/dev/null || true

echo
echo "Done. Now run: docker compose up --build"
