#!/usr/bin/env sh
# Rivulets install script (curl | sh pattern).
# docs/infrastructure/deployment-and-networking.md#distribution-channels
#
#   curl -fsSL https://raw.githubusercontent.com/jwilson411/Rivulets/main/scripts/install.sh | sh
#
# (get.rivulets.io is the intended short URL once that domain points here —
# not live yet, so use the raw.githubusercontent.com form above for now.)
#
# Detects OS/arch, downloads the matching release binary from GitHub
# Releases, verifies its SHA-256 checksum, and installs it on PATH.
set -eu

REPO="${RIVULETS_REPO:-jwilson411/Rivulets}"
INSTALL_DIR="${RIVULETS_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${RIVULETS_VERSION:-latest}"

os() {
	case "$(uname -s)" in
	Linux) echo linux ;;
	Darwin) echo darwin ;;
	*)
		echo "Unsupported OS: $(uname -s)" >&2
		exit 1
		;;
	esac
}

arch() {
	case "$(uname -m)" in
	x86_64 | amd64) echo amd64 ;;
	arm64 | aarch64) echo arm64 ;;
	*)
		echo "Unsupported architecture: $(uname -m)" >&2
		exit 1
		;;
	esac
}

OS="$(os)"
ARCH="$(arch)"
ASSET="rivulets-${OS}-${ARCH}"

if [ "$VERSION" = "latest" ]; then
	BASE_URL="https://github.com/${REPO}/releases/latest/download"
else
	BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${ASSET} (${VERSION})..."
curl -fsSL "${BASE_URL}/${ASSET}" -o "${TMP_DIR}/${ASSET}"
curl -fsSL "${BASE_URL}/${ASSET}.sha256" -o "${TMP_DIR}/${ASSET}.sha256"

echo "Verifying checksum..."
(cd "$TMP_DIR" && sha256sum -c "${ASSET}.sha256" 2>/dev/null || shasum -a 256 -c "${ASSET}.sha256")

mkdir -p "$INSTALL_DIR"
install -m 0755 "${TMP_DIR}/${ASSET}" "${INSTALL_DIR}/rivulets"

echo "Installed to ${INSTALL_DIR}/rivulets"
case ":$PATH:" in
*":${INSTALL_DIR}:"*) ;;
*) echo "Add ${INSTALL_DIR} to your PATH, then run: rivulets" ;;
esac
