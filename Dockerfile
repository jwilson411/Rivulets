# syntax=docker/dockerfile:1
#
# Three stages: build the SvelteKit UI, install the Python server + its
# deps, then assemble both into a slim runtime image. Versions (Python
# 3.12, Node 22) match .github/workflows/ci.yml so this builds against the
# same toolchain CI already verifies against.
#
# Base images are pinned by digest, not just tag -- a tag is mutable (a
# repush or upstream rebuild changes what gets built with no diff in this
# repo); the digest is what actually gets built. Bumping a version means
# updating both the tag (for readability) and the digest together --
# `docker buildx imagetools inspect <image>:<tag>` prints the current one.
#
# The built UI lands at server/src/rivulets/static — the exact path
# rivulets.app._static_dir() looks for when running unfrozen (see that
# function's docstring). This is the same layout scripts/build-all.sh's
# non-Docker PyInstaller path produces at build time, via a different
# mechanism (packaging/_common.py's bundled `datas`).

FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS ui-builder
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS server-builder
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

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS runtime
LABEL org.opencontainers.image.source="https://github.com/jwilson411/Rivulets"
LABEL org.opencontainers.image.description="Local-first, Slack-like workspace for humans and AI agent teams"
LABEL org.opencontainers.image.licenses="BUSL-1.1"

# firejail is deliberately NOT installed here. It needs to create its own
# mount/user namespaces, which needs CAP_SYS_ADMIN — a capability outside
# Docker's default bounding set. Installing the binary without also
# granting that capability (and relaxing the container's seccomp/AppArmor
# profile to allow the syscalls it uses) would just leave firejail present
# but non-functional (code_exec.is_available() probes for exactly that
# state and reports the tool unavailable rather than advertising one that
# can only fail). Granting SYS_ADMIN to every
# container by default to support this one opt-in tool would weaken the
# baseline hardening this image otherwise ships with for everyone, for a
# feature most Docker installs won't use. Instead, the Code Execution
# built-in tool reports itself unavailable under a stock `docker compose
# up` (see tools/builtin/code_exec.py's is_available()) and fails closed
# rather than running unsandboxed. See docs/security.md for the opt-in
# --cap-add/--security-opt profile for installs that want it anyway.

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
