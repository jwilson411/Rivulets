# Rivulets install script for Windows (irm | iex pattern).
#
#   irm https://raw.githubusercontent.com/jwilson411/Rivulets/v0.7.1/scripts/install.ps1 | iex
#
# Pinned to a tagged release, not `main`, for the same reason install.sh is:
# main is a live branch a compromised account/token could push arbitrary
# commits to, and this script -- fetched fresh on every `irm | iex` run, not
# something users re-review each time -- is exactly the kind of thing such a
# commit would target (e.g. quietly dropping the cosign check below). Bump
# the pinned tag here (and in README.md's Quick Install commands) on every
# release.
#
# Downloads the Windows release binary from GitHub Releases, verifies its
# SHA-256 checksum and its Sigstore signature (same identity/issuer check as
# scripts/install.sh and update.py), installs it to a per-user directory,
# and puts that directory on the user PATH. Fails closed if cosign isn't
# available -- pass -InsecureChecksumOnly (or set
# $env:RIVULETS_INSECURE_CHECKSUM_ONLY = "1" when piping through iex, which
# can't forward parameters) to accept checksum-only verification.
[CmdletBinding()]
param(
	[switch]$InsecureChecksumOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# Invoke-WebRequest's progress bar slows downloads by orders of magnitude on
# Windows PowerShell 5.1.
$ProgressPreference = "SilentlyContinue"

# Windows PowerShell 5.1 defaults can exclude TLS 1.2 on older builds; GitHub
# requires it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if (-not $InsecureChecksumOnly -and $env:RIVULETS_INSECURE_CHECKSUM_ONLY -eq "1") {
	$InsecureChecksumOnly = $true
}

# Refuse a RIVULETS_REPO override by default: this script is meant to be
# piped straight into `iex`, so an env var alone (no code/filesystem access
# needed) could otherwise silently redirect the install to an
# attacker-controlled fork. RIVULETS_ALLOW_REPO_OVERRIDE=1 is the explicit
# opt-in for testing against a fork.
$DefaultRepo = "jwilson411/Rivulets"
$Repo = if ($env:RIVULETS_REPO) { $env:RIVULETS_REPO } else { $DefaultRepo }
if ($Repo -ne $DefaultRepo -and $env:RIVULETS_ALLOW_REPO_OVERRIDE -ne "1") {
	Write-Warning "RIVULETS_REPO=$Repo ignored -- set RIVULETS_ALLOW_REPO_OVERRIDE=1 to install from a non-default repo."
	$Repo = $DefaultRepo
}
$InstallDir = if ($env:RIVULETS_INSTALL_DIR) { $env:RIVULETS_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Rivulets\bin" }
$Version = if ($env:RIVULETS_VERSION) { $env:RIVULETS_VERSION } else { "latest" }

# Only an amd64 Windows binary is built (see release.yml's matrix). Windows 11
# on ARM runs it under x64 emulation; 32-bit-only hosts can't run it at all.
$HostArch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) {
	# 32-bit PowerShell on a 64-bit OS reports x86; the real arch is here.
	$HostArch = $env:PROCESSOR_ARCHITEW6432
}
switch ($HostArch) {
	"AMD64" { }
	"ARM64" { Write-Warning "No native Windows arm64 build yet -- installing the amd64 binary, which runs under x64 emulation on Windows 11 on ARM." }
	default {
		throw "Unsupported architecture: $HostArch (only 64-bit Windows is supported)."
	}
}

$Asset = "rivulets-windows-amd64.exe"

if ($Version -eq "latest") {
	$BaseUrl = "https://github.com/$Repo/releases/latest/download"
} else {
	$BaseUrl = "https://github.com/$Repo/releases/download/$Version"
}

$TmpDir = Join-Path ([IO.Path]::GetTempPath()) "rivulets-install-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $TmpDir | Out-Null
try {
	Write-Host "Downloading $Asset ($Version)..."
	Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset" -OutFile (Join-Path $TmpDir $Asset)
	Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset.sha256" -OutFile (Join-Path $TmpDir "$Asset.sha256")

	Write-Host "Verifying checksum..."
	# release.yml's Checksum step writes sha256sum(1)/shasum(1) format:
	# "<hex digest>  <filename>" (the filename may carry a leading `*`).
	$ChecksumLine = (Get-Content (Join-Path $TmpDir "$Asset.sha256") -TotalCount 1).Trim()
	$Expected, $ChecksumName = $ChecksumLine -split "\s+", 2
	if (-not $ChecksumName) {
		throw "Malformed checksum file: '$ChecksumLine'."
	}
	if ($ChecksumName.TrimStart("*") -ne $Asset) {
		throw "Checksum file names '$ChecksumName', expected '$Asset'."
	}
	$Actual = (Get-FileHash -Algorithm SHA256 (Join-Path $TmpDir $Asset)).Hash
	if ($Actual -ne $Expected) {
		throw "Checksum mismatch for ${Asset}: expected $Expected, got $Actual."
	}

	# A checksum alone only proves the download wasn't corrupted in transit --
	# it doesn't prove the release itself came from this project's own CI,
	# since the checksum is fetched from the same GitHub Releases origin as
	# the binary. release.yml also signs every binary (keyless, via cosign +
	# GitHub's own OIDC identity -- no static key involved) and publishes the
	# signature/certificate alongside it; verify that too, and fail closed if
	# cosign isn't there to do it -- a compromised release (or a redirected
	# RIVULETS_REPO) is enough to make checksum-only verification pass on a
	# malicious binary, so skipping this silently would defeat the point.
	if (Get-Command cosign -ErrorAction SilentlyContinue) {
		Write-Host "Verifying Sigstore signature..."
		Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset.sig" -OutFile (Join-Path $TmpDir "$Asset.sig")
		Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset.pem" -OutFile (Join-Path $TmpDir "$Asset.pem")
		& cosign verify-blob `
			--certificate (Join-Path $TmpDir "$Asset.pem") `
			--signature (Join-Path $TmpDir "$Asset.sig") `
			--certificate-identity-regexp "^https://github.com/$Repo/.github/workflows/release.yml@.+$" `
			--certificate-oidc-issuer "https://token.actions.githubusercontent.com" `
			(Join-Path $TmpDir $Asset)
		if ($LASTEXITCODE -ne 0) {
			throw "Sigstore signature verification failed (cosign exit code $LASTEXITCODE)."
		}
	} elseif ($InsecureChecksumOnly) {
		Write-Warning "cosign not found on PATH -- skipping Sigstore signature verification (checksum-only, as requested via -InsecureChecksumOnly)."
	} else {
		throw ("cosign not found on PATH -- refusing to install with checksum-only verification.`n" +
			"Install cosign (https://docs.sigstore.dev/cosign/system_config/installation/) for cryptographic provenance verification, " +
			"or re-run with -InsecureChecksumOnly (`$env:RIVULETS_INSECURE_CHECKSUM_ONLY = `"1`" when piping through iex) to accept checksum-only verification.")
	}

	New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
	Copy-Item (Join-Path $TmpDir $Asset) (Join-Path $InstallDir "rivulets.exe") -Force
} finally {
	Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}

Write-Host "Installed to $(Join-Path $InstallDir 'rivulets.exe')"

# Unlike ~/.local/bin on Linux, no per-user bin directory is ever on PATH by
# default on Windows, so put the install directory on the user PATH (HKCU
# only -- no elevation, no machine-wide change) rather than just telling the
# user to.
$NormalizedInstallDir = $InstallDir.TrimEnd("\")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$OnUserPath = ($UserPath -split ";") | Where-Object { $_.TrimEnd("\") -eq $NormalizedInstallDir }
if (-not $OnUserPath) {
	$NewUserPath = if ([string]::IsNullOrEmpty($UserPath)) { $InstallDir } else { $UserPath.TrimEnd(";") + ";" + $InstallDir }
	[Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
	Write-Host "Added $InstallDir to your user PATH (takes effect in new terminals)."
}
$OnSessionPath = ($env:Path -split ";") | Where-Object { $_.TrimEnd("\") -eq $NormalizedInstallDir }
if (-not $OnSessionPath) {
	$env:Path = "$env:Path;$InstallDir"
}
Write-Host "Run: rivulets"
