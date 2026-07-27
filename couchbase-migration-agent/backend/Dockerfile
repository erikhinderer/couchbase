# --- Couchbase Server Tools (cbbackupmgr, cbrestore, cbimport, ...) -----------
# Earlier versions of this Dockerfile tried to `apt-get install` these from
# Couchbase's package repo (packages.couchbase.com) at build time. That endpoint
# is gated for lead-gen tracking and returns 403 to non-browser/automated
# requests fairly often, which breaks the build unpredictably depending on the
# network it's built from.
#
# Instead we pull the binaries straight out of Couchbase's own official Docker
# Hub image -- the same Enterprise Edition image docker-compose.yml already
# uses for the agent memory store -- via a multi-stage build. That pull goes
# through Docker Hub's registry, not packages.couchbase.com, so it isn't
# subject to the same gating, and it guarantees a binary that's known-good for
# the pinned version.
ARG COUCHBASE_TOOLS_VERSION=7.6.2
FROM couchbase:enterprise-${COUCHBASE_TOOLS_VERSION} AS couchbase-tools

FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy just the CLI tools (and their shared libs) out of the official Couchbase
# image -- not the whole server -- so the final image stays small and never
# tries to run couchbase-server itself.
COPY --from=couchbase-tools /opt/couchbase/bin /opt/couchbase/bin
COPY --from=couchbase-tools /opt/couchbase/lib /opt/couchbase/lib

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# NOTE: deliberately NOT setting LD_LIBRARY_PATH here as a container-wide env
# var. /opt/couchbase/lib ships its own libcrypto.so.3/libssl.so.3 built for a
# specific OpenSSL ABI (observed: symbol version OPENSSL_3.3.0 missing against
# python:3.11-slim's OpenSSL) -- if the dynamic linker resolves those before
# the system libs for every process in the container, `python -c "import ssl"`
# itself fails at import time and uvicorn can't even start
# ("ImportError: /opt/couchbase/lib/libcrypto.so.3: version 'OPENSSL_3.3.0'
# not found"). cbbackupmgr/cbrestore are the only things that need Couchbase's
# bundled libs; BackupManager._run() in app/core/backup_manager.py sets
# LD_LIBRARY_PATH just for that subprocess instead of polluting the whole
# container's environment.
ENV PYTHONUNBUFFERED=1 \
    COUCHBASE_BIN_DIR=/opt/couchbase/bin \
    COUCHBASE_LIB_DIR=/opt/couchbase/lib

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
