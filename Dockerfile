# syntax=docker/dockerfile:1
#
# Three stages: build the SvelteKit UI, install the Python server + its
# deps, then assemble both into a slim runtime image. Versions (Python
# 3.12, Node 22) match .github/workflows/ci.yml so this builds against the
# same toolchain CI already verifies against.
#
# The built UI lands at server/src/rivulets/static — the exact path
# rivulets.app._static_dir() looks for when running unfrozen (see that
# function's docstring). This is the same layout scripts/build-all.sh's
# non-Docker PyInstaller path produces at build time, via a different
# mechanism (packaging/_common.py's bundled `datas`).

FROM node:22-slim AS ui-builder
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS server-builder
WORKDIR /app/server
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# libp2p -> fastecdsa has no prebuilt wheel for every platform this builds
# on (e.g. linux/arm64) and compiles a C extension (against libgmp) on
# install — build-time only, this layer and its toolchain never reach the
# runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgmp-dev \
    && rm -rf /var/lib/apt/lists/*
# Dependencies first, in their own layer, before the source that changes on
# every commit — avoids a full `uv sync` on every source-only rebuild.
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY server/ ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
LABEL org.opencontainers.image.source="https://github.com/jwilson411/Rivulets"
LABEL org.opencontainers.image.description="Local-first, Slack-like workspace for humans and AI agent teams"
LABEL org.opencontainers.image.licenses="BUSL-1.1"

RUN useradd --create-home --home-dir /home/rivulets --shell /usr/sbin/nologin rivulets

WORKDIR /app/server
COPY --from=server-builder /app/server /app/server
COPY --from=ui-builder /src/ui/build /app/server/src/rivulets/static

ENV PATH="/app/server/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RIVULETS_WORKSPACE_DIR=/data \
    RIVULETS_APP_SERVER_HOST=0.0.0.0

# /data is where workspace state (SQLite db, files, keys, logs) lives —
# always mount a volume here, or every restart starts a fresh workspace.
# See main.py's host-guard comment for why 0.0.0.0 is safe here: actual
# exposure is controlled by this image's own EXPOSE + whatever `-p` flag
# a `docker run`/compose file uses to publish it, not by this bind address.
RUN mkdir -p /data && chown -R rivulets:rivulets /data /app/server

USER rivulets
EXPOSE 8484
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8484/api/v1/health', timeout=2)" || exit 1

ENTRYPOINT ["rivulets"]
