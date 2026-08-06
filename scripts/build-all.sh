#!/usr/bin/env bash
# Local build helper — builds the UI, then produces a PyInstaller binary
# for the current host platform via packaging/{linux,macos,windows}.spec.
#
# CI (.github/workflows/release.yml) runs the equivalent steps per
# platform in the build matrix; this script is for local testing of a
# single-platform build without pushing a release tag.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

spec_for_platform() {
	case "$(uname -s)" in
	Linux) echo packaging/linux.spec ;;
	Darwin) echo packaging/macos.spec ;;
	MINGW* | MSYS* | CYGWIN*) echo packaging/windows.spec ;;
	*)
		echo "Unsupported platform: $(uname -s)" >&2
		exit 1
		;;
	esac
}

echo "==> Building UI"
(cd ui && npm install && npm run build)

echo "==> Installing server + packaging deps"
(cd server && uv sync --group packaging)

SPEC="$(spec_for_platform)"
echo "==> Running PyInstaller (${SPEC})"
(cd server && uv run --group packaging pyinstaller "../${SPEC}" --distpath "../dist" --workpath "../build/pyinstaller")

echo "==> Done. Binaries in ./dist/"
ls -la dist/
