#!/usr/bin/env sh
# Rivulets install script (curl | sh pattern).
#
#   curl -fsSL https://raw.githubusercontent.com/jwilson411/Rivulets/main/scripts/install.sh | sh
#
# (get.rivulets.io is the intended short URL once that domain points here —
# not live yet, so use the raw.githubusercontent.com form above for now.)
#
# Detects OS/arch, downloads the matching release binary from GitHub
# Releases, verifies its SHA-256 checksum (and its Sigstore signature, if
# cosign is on PATH -- see below), and installs it on PATH.
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

# No darwin-amd64 release asset (Intel Mac) yet -- see release.yml for why.
if [ "$OS" = "darwin" ] && [ "$ARCH" = "amd64" ]; then
	echo "No native build for Intel Macs yet -- use Docker or build from source." >&2
	echo "See: https://github.com/${REPO}#installation" >&2
	exit 1
fi

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

# A checksum alone only proves the download wasn't corrupted in transit --
# it doesn't prove the release itself came from this project's own CI, since
# the checksum is fetched from the same GitHub Releases origin as the
# binary. release.yml also signs every binary (keyless, via cosign +
# GitHub's own OIDC identity -- no static key involved) and publishes the
# signature/certificate alongside it; verify that too when cosign is
# available, same "degrade gracefully, don't hard-require an extra tool"
# pattern this project uses elsewhere for optional capabilities.
if command -v cosign >/dev/null 2>&1; then
	echo "Verifying Sigstore signature..."
	curl -fsSL "${BASE_URL}/${ASSET}.sig" -o "${TMP_DIR}/${ASSET}.sig"
	curl -fsSL "${BASE_URL}/${ASSET}.pem" -o "${TMP_DIR}/${ASSET}.pem"
	cosign verify-blob \
		--certificate "${TMP_DIR}/${ASSET}.pem" \
		--signature "${TMP_DIR}/${ASSET}.sig" \
		--certificate-identity-regexp "^https://github.com/${REPO}/.github/workflows/release.yml@.+\$" \
		--certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
		"${TMP_DIR}/${ASSET}"
else
	echo "cosign not found on PATH -- skipping Sigstore signature verification (checksum-only)." >&2
	echo "Install cosign (https://docs.sigstore.dev/cosign/system_config/installation/) for cryptographic provenance verification of releases." >&2
fi

mkdir -p "$INSTALL_DIR"
install -m 0755 "${TMP_DIR}/${ASSET}" "${INSTALL_DIR}/rivulets"

echo "Installed to ${INSTALL_DIR}/rivulets"
case ":$PATH:" in
*":${INSTALL_DIR}:"*) ;;
*) echo "Add ${INSTALL_DIR} to your PATH, then run: rivulets" ;;
esac
