#!/usr/bin/env python3
"""Authenticode-sign Windows EXEs for SmartScreen / Chrome download trust.

Chrome's "isn't commonly downloaded" and Windows SmartScreen both improve when
Setup.exe + the payload EXE are signed with a real code-signing certificate.
A local release and the GitHub Actions release sign through this same script.

Settings (environment first; keys still unset come from the .env files in
dotenv_paths(): this checkout, the main checkout when run from a worktree,
../DuckyOS, ~/.duckyos; first match wins, a blank value counts as unset):

  DUCKY_SIGN_PROVIDER
      azure     Azure Artifact Signing (signtool + the Artifact Signing dlib).
      pfx       A .pfx/.p12 file (DUCKY_WINDOWS_PFX + DUCKY_WINDOWS_PFX_PASSWORD).
      signtool  signtool with your own arguments (DUCKY_SIGNTOOL_EXTRA), e.g.
                DigiCert KeyLocker: /csp "DigiCert Signing Manager KSP" /kc <alias> /f <cert.crt>
      blank     pfx when DUCKY_WINDOWS_PFX is set, signtool when
                DUCKY_SIGNTOOL_EXTRA is set, otherwise unsigned.
      none/off  unsigned.

  Azure:
    AZURE_TRUSTED_SIGNING_ENDPOINT   account URI, e.g. https://eus.codesigning.azure.net
    AZURE_TRUSTED_SIGNING_ACCOUNT    Artifact Signing account name
    AZURE_CERT_PROFILE_NAME          certificate profile name
    DUCKY_AZURE_AUTH                 cli (az login), environment (AZURE_CLIENT_ID /
                                     AZURE_TENANT_ID / AZURE_CLIENT_SECRET), blank =
                                     Azure's default sign-in chain
    DUCKY_AZURE_DLIB                 Azure.CodeSigning.Dlib.dll; blank = found
                                     automatically (client tools / NuGet)

  DUCKY_SIGNTOOL            signtool.exe; blank = PATH, Windows Kits, client tools
  DUCKY_SIGNTOOL_EXTRA      extra signtool sign arguments (quotes group words,
                            backslashes stay as typed)
  DUCKY_SIGN_TIMESTAMP_URL  blank = DigiCert, or Microsoft's for Azure
  DUCKY_SIGNING_PROFILE_EKU optional: every signed file must carry this EKU OID

Usage:
  py release/sign_windows.py --check                    # ready to sign? (plain words)
  py release/sign_windows.py --check dist/UEFN-Ducky-Setup-1.2.343.exe   # + who signed it
  py release/sign_windows.py --require dist/UEFN-Ducky-Setup-1.2.343.exe

Exit codes:
  0  signed / ready (or skipped because signing is off and --require not set)
  2  --require or --check, but signing is not configured
  1  signtool failed / --check found something missing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"
AZURE_TIMESTAMP = "http://timestamp.acs.microsoft.com"
# Everything signing (and the installer build it drives) reads. publish_app loads
# these from every .env this script reads, so its "signing on?" answer (custom
# Setup host or not) matches what actually gets signed.
SIGNING_KEYS = (
    "DUCKY_SIGN_PROVIDER",
    "DUCKY_WINDOWS_PFX",
    "DUCKY_WINDOWS_PFX_PASSWORD",
    "DUCKY_SIGNTOOL",
    "DUCKY_SIGNTOOL_EXTRA",
    "DUCKY_SIGN_TIMESTAMP_URL",
    "DUCKY_SIGNING_PROFILE_EKU",
    "DUCKY_AZURE_AUTH",
    "DUCKY_AZURE_DLIB",
    "AZURE_TRUSTED_SIGNING_ENDPOINT",
    "AZURE_TRUSTED_SIGNING_ACCOUNT",
    "AZURE_CERT_PROFILE_NAME",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
    "INNO_SETUP_ISCC",
)
_AZURE_FIELDS = {
    "Endpoint": "AZURE_TRUSTED_SIGNING_ENDPOINT",
    "CodeSigningAccountName": "AZURE_TRUSTED_SIGNING_ACCOUNT",
    "CertificateProfileName": "AZURE_CERT_PROFILE_NAME",
}
# Azure.Identity's default chain; DUCKY_AZURE_AUTH keeps only one of them so a
# sign-in never wanders into a browser prompt or a stale cached account.
_AZURE_CREDENTIALS = (
    "EnvironmentCredential",
    "WorkloadIdentityCredential",
    "ManagedIdentityCredential",
    "SharedTokenCacheCredential",
    "VisualStudioCredential",
    "VisualStudioCodeCredential",
    "AzureCliCredential",
    "AzurePowerShellCredential",
    "AzureDeveloperCliCredential",
    "InteractiveBrowserCredential",
)
_AZURE_AUTH = {"cli": "AzureCliCredential", "environment": "EnvironmentCredential", "": None}
_AZURE_WHERE = {
    "AZURE_TRUSTED_SIGNING_ENDPOINT": "Azure portal > Artifact Signing Accounts > your account > Overview > Account URI.",
    "AZURE_TRUSTED_SIGNING_ACCOUNT": "Azure portal > Artifact Signing Accounts: the account's name.",
    "AZURE_CERT_PROFILE_NAME": "Azure portal > Artifact Signing Accounts > your account > Certificate profiles: the profile's name.",
}
_PROVIDERS = ("azure", "pfx", "signtool")
# Artifact Signing regions; the endpoint must be the account's own region or
# signing fails with 403 / SignerSign().
AZURE_ENDPOINT_RE = re.compile(
    r"^https://(brs|cus|eus|jpe|krc|ncus|neu|plc|scus|swn|wcus|weu|wus|wus2|wus3)\.codesigning\.azure\.net/?$",
    re.IGNORECASE,
)
# The dlib needs signtool from Windows SDK 10.0.22621.755 or newer (20348 does not work).
AZURE_MIN_SIGNTOOL = (10, 0, 22621, 755)
# Azure location of an account -> the region code in its endpoint.
AZURE_REGION_CODES = {
    "brazilsouth": "brs",
    "centralus": "cus",
    "eastus": "eus",
    "japaneast": "jpe",
    "koreacentral": "krc",
    "northcentralus": "ncus",
    "northeurope": "neu",
    "polandcentral": "plc",
    "southcentralus": "scus",
    "switzerlandnorth": "swn",
    "westcentralus": "wcus",
    "westeurope": "weu",
    "westus": "wus",
    "westus2": "wus2",
    "westus3": "wus3",
}
AZURE_SIGNER_ROLE = "Artifact Signing Certificate Profile Signer"
_ARM = "https://management.azure.com"
_CODESIGNING_API = "2024-09-30-preview"


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def main_checkout(root: Path = ROOT) -> Path | None:
    """The main checkout when ``root`` is a linked worktree (its .git is a file)."""
    marker = root / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text(encoding="utf-8-sig").strip()
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.partition(":")[2].strip())
    if not gitdir.is_absolute():
        gitdir = (root / gitdir).resolve()
    # <main>/.git/worktrees/<name>
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        return None
    return gitdir.parent.parent.parent


def dotenv_paths(root: Path = ROOT, *, duckyos: bool = True) -> list[Path]:
    """The .env files a release reads, first match wins. A release run from a
    worktree (ducky-rel) also reads the main checkout's .env."""
    paths = [root / ".env"]
    main = main_checkout(root)
    if main is not None:
        paths.append(main / ".env")
    if duckyos:
        paths.append(root.parent / "DuckyOS" / ".env")
    paths.append(Path.home() / ".duckyos" / ".env")
    return paths


def _unquote(val: str) -> str:
    # Only a matching outer pair: DUCKY_SIGNTOOL_EXTRA can end in a quoted
    # path (/f "C:\\a b\\cert.crt") and must keep the quote that opened it.
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val


def load_dotenv(paths: list[Path], keys: tuple[str, ...] | None = None) -> None:
    """Fill unset env vars from ``paths`` (only ``keys`` when given). A blank
    value (KEY=) counts as unset, so an empty line in one file never hides a
    value in a later one."""
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), _unquote(val.strip())
            if keys is not None and key not in keys:
                continue
            if key and val and not (os.environ.get(key) or "").strip():
                os.environ[key] = val


def _load_dotenv() -> None:
    load_dotenv(dotenv_paths())


def extra_args() -> list[str]:
    """DUCKY_SIGNTOOL_EXTRA as argv: quotes group words, backslashes stay as typed."""
    extra = _env("DUCKY_SIGNTOOL_EXTRA")
    if not extra:
        return []
    return [_unquote(part) for part in shlex.split(extra, posix=False)]


# ---------------------------------------------------------------------------
# Which signing, and where the tools are
# ---------------------------------------------------------------------------


def signing_mode() -> str | None:
    """'azure', 'pfx' or 'signtool'; None when releases are unsigned. Any other
    DUCKY_SIGN_PROVIDER value comes back as-is so it fails loudly, not unsigned."""
    provider = _env("DUCKY_SIGN_PROVIDER").lower()
    if provider in ("none", "off", "unsigned"):
        return None
    if provider:
        return provider
    if _env("DUCKY_WINDOWS_PFX"):
        return "pfx"
    if _env("DUCKY_SIGNTOOL_EXTRA"):
        return "signtool"
    return None


def signing_configured() -> bool:
    return signing_mode() is not None


def _version_key(path: Path) -> tuple:
    # Prefer an x64 build, then the highest version folder in the path.
    nums = tuple(int(n) for part in path.parts for n in part.split(".") if n.isdigit())
    return ("x64" in [p.lower() for p in path.parts], nums)


def _newest(paths: list[Path]) -> Path | None:
    return max(paths, key=_version_key) if paths else None


def _azure_tool_dirs() -> list[Path]:
    """Folders the Artifact Signing client tools (winget MSI or NuGet) live in."""
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    roots = [local / "Microsoft", local / "Programs"]
    roots += [Path(p) for p in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if p]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found += [d for d in root.glob("*SigningClientTools*") if d.is_dir()]
            found += [d for d in root.glob("*/*SigningClientTools*") if d.is_dir()]
    for pkg in ("microsoft.artifactsigning.client", "microsoft.trusted.signing.client"):
        if (_nuget_packages() / pkg).is_dir():
            found.append(_nuget_packages() / pkg)
    return found


def _nuget_packages() -> Path:
    return Path(os.environ.get("NUGET_PACKAGES") or (Path.home() / ".nuget" / "packages"))


def find_signtool() -> Path | None:
    override = _env("DUCKY_SIGNTOOL")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    which = shutil.which("signtool")
    if which:
        return Path(which)
    candidates: list[Path] = []
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    if kits.is_dir():
        candidates += [d / "x64" / "signtool.exe" for d in kits.iterdir() if (d / "x64" / "signtool.exe").is_file()]
    for tools in _azure_tool_dirs():
        candidates += list(tools.rglob("signtool.exe"))
    buildtools = _nuget_packages() / "microsoft.windows.sdk.buildtools"
    if buildtools.is_dir():
        candidates += list(buildtools.glob("*/bin/*/x64/signtool.exe"))
    return _newest(candidates)


def find_azure_dlib() -> Path | None:
    override = _env("DUCKY_AZURE_DLIB")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    found: list[Path] = []
    for tools in _azure_tool_dirs():
        found += list(tools.rglob("Azure.CodeSigning.Dlib.dll"))
    return _newest(found)


def azure_metadata() -> dict:
    """The /dmdf JSON for Azure Artifact Signing. No secrets: sign-in is Azure's own."""
    meta: dict = {field: _env(key) for field, key in _AZURE_FIELDS.items()}
    keep = _AZURE_AUTH.get(_env("DUCKY_AZURE_AUTH").lower())
    if keep:
        meta["ExcludeCredentials"] = [c for c in _AZURE_CREDENTIALS if c != keep]
    return meta


def timestamp_url() -> str:
    return _env("DUCKY_SIGN_TIMESTAMP_URL") or (AZURE_TIMESTAMP if signing_mode() == "azure" else DEFAULT_TIMESTAMP)


# ---------------------------------------------------------------------------
# Sign + verify
# ---------------------------------------------------------------------------


def _pfx_args() -> list[str]:
    pfx = _env("DUCKY_WINDOWS_PFX")
    if not pfx:
        raise SystemExit("DUCKY_SIGN_PROVIDER=pfx needs DUCKY_WINDOWS_PFX (the .pfx file)")
    path = Path(pfx)
    if not path.is_file():
        raise SystemExit(f"DUCKY_WINDOWS_PFX not found: {path}")
    password = os.environ.get("DUCKY_WINDOWS_PFX_PASSWORD")
    if password is None:
        raise SystemExit("DUCKY_WINDOWS_PFX_PASSWORD is required when using DUCKY_WINDOWS_PFX")
    return ["/f", str(path), "/p", password]


def _azure_args(metadata_file: Path) -> list[str]:
    meta = azure_metadata()
    missing = [key for field, key in _AZURE_FIELDS.items() if not meta[field]]
    if missing:
        raise SystemExit(f"DUCKY_SIGN_PROVIDER=azure needs {', '.join(missing)}")
    auth = _env("DUCKY_AZURE_AUTH").lower()
    if auth not in _AZURE_AUTH:
        raise SystemExit(f"DUCKY_AZURE_AUTH must be cli, environment or blank, not {auth!r}")
    dlib = find_azure_dlib()
    if dlib is None:
        raise SystemExit(
            "Azure.CodeSigning.Dlib.dll not found. Install the client tools "
            "(winget install -e --id Microsoft.Azure.ArtifactSigningClientTools) or set DUCKY_AZURE_DLIB."
        )
    metadata_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return ["/dlib", str(dlib), "/dmdf", str(metadata_file)]


def _credential_args(mode: str | None, tmp: Path) -> list[str]:
    if mode == "azure":
        return _azure_args(tmp / "metadata.json")
    if mode == "pfx":
        return _pfx_args()
    if mode == "signtool":
        if not extra_args():
            raise SystemExit("DUCKY_SIGN_PROVIDER=signtool needs DUCKY_SIGNTOOL_EXTRA (your signtool arguments)")
        return []
    raise SystemExit(f"DUCKY_SIGN_PROVIDER must be one of {', '.join(_PROVIDERS)} (or blank/none), not {mode!r}")


def _redacted(cmd: list[str]) -> str:
    out: list[str] = []
    hide = False
    for part in cmd:
        out.append("***" if hide else part)
        hide = part.lower() == "/p"
    return " ".join(out)


def sign_file(path: Path, *, signtool: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Cannot sign missing file: {path}")
    with tempfile.TemporaryDirectory(prefix="ducky-sign-") as tmp:
        mode = signing_mode()
        cmd = [
            str(signtool),
            "sign",
            *(["/v"] if mode == "azure" else []),
            "/fd",
            "sha256",
            "/td",
            "sha256",
            "/tr",
            timestamp_url(),
            *_credential_args(mode, Path(tmp)),
            *extra_args(),
            str(path),
        ]
        # Never print the password: the /p value is redacted in the log.
        print(f"  signtool: {_redacted(cmd)}")
        subprocess.run(cmd, check=True)


_SIGNATURE_PROBE = r"""
$s = Get-AuthenticodeSignature -LiteralPath $env:DUCKY_PROBE_FILE
$c = $s.SignerCertificate
$eku = @()
if ($c) { $eku = @($c.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value }) }
[pscustomobject]@{
  status = "$($s.Status)"; message = "$($s.StatusMessage)"
  subject = $(if ($c) { $c.Subject } else { '' }); thumbprint = $(if ($c) { $c.Thumbprint } else { '' })
  timestamped = [bool]$s.TimeStamperCertificate; eku = $eku
} | ConvertTo-Json -Compress
"""


def signature_info(path: Path) -> dict:
    """Who signed ``path`` (Get-AuthenticodeSignature): status, signer, timestamp, EKUs."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SIGNATURE_PROBE],
        capture_output=True,
        text=True,
        env={**os.environ, "DUCKY_PROBE_FILE": str(path)},
        timeout=120,
    )
    try:
        info = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"status": "Unknown", "message": (proc.stderr or "").strip(), "eku": []}
    eku = info.get("eku") or []
    info["eku"] = [eku] if isinstance(eku, str) else list(eku)
    return info


def verify_file(path: Path, *, signtool: Path) -> None:
    subprocess.run([str(signtool), "verify", "/pa", str(path)], check=True)
    want = _env("DUCKY_SIGNING_PROFILE_EKU")
    if want:
        info = signature_info(path)
        if want not in info["eku"]:
            raise SystemExit(
                f"{path.name} is signed by {info.get('subject') or 'nobody'}, whose certificate lacks "
                f"DUCKY_SIGNING_PROFILE_EKU {want} (it has: {', '.join(info['eku']) or 'none'})"
            )
        print(f"  signer EKU {want}: ok")


# ---------------------------------------------------------------------------
# --check: is this PC ready to sign a release? Signs nothing, prints no secret.
# ---------------------------------------------------------------------------

_PFX_PROBE = r"""
$ErrorActionPreference = 'Stop'
$flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
$c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($env:DUCKY_PROBE_PFX, $env:DUCKY_PROBE_PW, $flags)
$eku = @($c.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
[pscustomobject]@{
  subject = $c.Subject; issuer = $c.Issuer; thumbprint = $c.Thumbprint
  notAfter = $c.NotAfter.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss'); hasPrivateKey = $c.HasPrivateKey
  codeSigning = ($eku -contains '1.3.6.1.5.5.7.3.3'); selfSigned = ($c.Subject -eq $c.Issuer)
} | ConvertTo-Json -Compress
"""


def _probe_pfx(path: Path, password: str) -> dict:
    """Open the PFX in memory (never a certificate store) and read its certificate."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PFX_PROBE],
        capture_output=True,
        text=True,
        env={**os.environ, "DUCKY_PROBE_PFX": str(path), "DUCKY_PROBE_PW": password},
        timeout=60,
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        lines = (proc.stderr or "").strip().splitlines()
        reason = next((ln for ln in lines if "password" in ln.lower()), lines[0] if lines else "")
        return {"error": reason.strip() or "could not open it"}


def _run_quiet(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _file_version(path: Path) -> tuple[int, ...]:
    # The numeric parts: signtool's FileVersion *string* is "4.00 (WinBuild...)"
    # while its real version is 10.0.26100.x.
    script = (
        f"$v = (Get-Item -LiteralPath '{path}').VersionInfo; "
        "'{0}.{1}.{2}.{3}' -f $v.FileMajorPart, $v.FileMinorPart, $v.FileBuildPart, $v.FilePrivatePart"
    )
    proc = _run_quiet(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    text = ((proc.stdout if proc else "") or "").strip()
    try:
        return tuple(int(n) for n in text.split("."))
    except ValueError:
        return ()


def find_iscc() -> Path | None:
    """The same search make_release_installer.ps1 does."""
    for cand in (
        _env("INNO_SETUP_ISCC"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    ):
        if cand and Path(cand).is_file():
            return Path(cand)
    which = shutil.which("iscc")
    return Path(which) if which else None


def _dotnet_sdks() -> tuple[Path | None, list[str]]:
    """The dotnet make_release_installer.ps1 builds the Setup host with, and its SDKs."""
    hosts = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "dotnet" / "dotnet.exe",
        Path(os.environ.get("ProgramFiles", "")) / "dotnet" / "dotnet.exe",
    ]
    which = shutil.which("dotnet")
    if which:
        hosts.append(Path(which))
    for host in hosts:
        if host.is_file():
            proc = _run_quiet([str(host), "--list-sdks"])
            sdks = [ln.split(" ")[0] for ln in ((proc.stdout if proc else "") or "").splitlines() if ln.strip()]
            if sdks:
                return host, sdks
    return (next((h for h in hosts if h.is_file()), None), [])


class _Report:
    def __init__(self) -> None:
        self.missing: list[str] = []

    def ok(self, text: str) -> None:
        print(f"  ready    {text}")

    def miss(self, text: str) -> None:
        self.missing.append(text)
        print(f"  MISSING  {text}")

    def note(self, text: str) -> None:
        print(f"  note     {text}")


def _check_azure(r: _Report, signtool: Path | None) -> None:
    for key in _AZURE_FIELDS.values():
        if _env(key):
            r.ok(f"{key} = {_env(key)}")
        else:
            r.miss(f"{key} is blank. {_AZURE_WHERE[key]}")
    endpoint = _env("AZURE_TRUSTED_SIGNING_ENDPOINT")
    if endpoint and not AZURE_ENDPOINT_RE.match(endpoint):
        r.miss(
            f"AZURE_TRUSTED_SIGNING_ENDPOINT {endpoint} is not an Artifact Signing endpoint. It is "
            "https://<region>.codesigning.azure.net for your account's own region (eus, wus2, weu, ...); "
            "the wrong region fails with 403."
        )
    dlib = find_azure_dlib()
    if dlib:
        r.ok(f"Artifact Signing client (dlib): {dlib}")
    elif _env("DUCKY_AZURE_DLIB"):
        r.miss(f"DUCKY_AZURE_DLIB points at a file that does not exist: {_env('DUCKY_AZURE_DLIB')}")
    else:
        r.miss("Artifact Signing client tools are not installed: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools")
    if signtool is not None:
        ver = _file_version(signtool)
        if ver and ver < AZURE_MIN_SIGNTOOL:
            r.miss(
                f"signtool {'.'.join(map(str, ver))} is too old for Artifact Signing (needs Windows SDK "
                "10.0.22621.755 or newer): winget install -e --id Microsoft.Azure.ArtifactSigningClientTools"
            )
        if "x86" in [part.lower() for part in signtool.parts] or "arm64" in [part.lower() for part in signtool.parts]:
            r.miss(f"use the x64 signtool with the x64 dlib, not {signtool}")
    auth = _env("DUCKY_AZURE_AUTH").lower()
    if auth not in _AZURE_AUTH:
        r.miss(f"DUCKY_AZURE_AUTH must be cli, environment or blank, not {auth!r}")
    elif auth == "environment":
        need = [k for k in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID") if not _env(k)]
        if not (_env("AZURE_CLIENT_SECRET") or _env("AZURE_CLIENT_CERTIFICATE_PATH")):
            need.append("AZURE_CLIENT_SECRET")
        if need:
            r.miss(f"DUCKY_AZURE_AUTH=environment signs in with an app registration; set {', '.join(need)}")
        else:
            r.ok("Azure sign-in: app registration (AZURE_CLIENT_ID / AZURE_TENANT_ID / secret)")
    role_checked = False
    if auth in _AZURE_AUTH and auth != "environment":
        az = shutil.which("az")
        if not az:
            r.miss("Azure CLI is not installed (it signs you in): winget install -e --id Microsoft.AzureCLI, then az login")
        else:
            who = _check_az_token(r, az)
            if who:
                role_checked = _check_az_account(r, az, who)
            if not auth:
                r.note("DUCKY_AZURE_AUTH is blank, so Azure tries every sign-in kind; cli is faster and never pops a browser")
    if not role_checked:
        r.note(
            f'the signing identity needs the role "{AZURE_SIGNER_ROLE}" on the account '
            "(Azure portal > Artifact Signing Accounts > your account > Access control)"
        )

AZURE_SIGNING_SCOPE = "https://codesigning.azure.net/.default"


def _check_az_token(r: _Report, az: str) -> str:
    """Signed in, and able to get the token signing needs (MFA can still be pending).
    Returns who is signed in when the token works, else ""."""
    proc = _run_quiet([az, "account", "show", "--query", "[user.name, tenantId]", "-o", "tsv"], timeout=90)
    lines = ((proc.stdout or "").split() if proc and proc.returncode == 0 else [])
    who, tenant = (lines + ["", ""])[:2]
    if not who:
        r.miss("Azure CLI is not signed in: run az login (once on this PC)")
        return ""
    # --query expiresOn: only the expiry is printed, never the token.
    token = _run_quiet(
        [az, "account", "get-access-token", "--scope", AZURE_SIGNING_SCOPE, "--query", "expiresOn", "-o", "tsv"],
        timeout=120,
    )
    if token is not None and token.returncode == 0 and (token.stdout or "").strip():
        r.ok(f"Azure CLI signed in as {who} (tenant {tenant}) and can get a code-signing token")
        return who
    err = ((token.stderr if token else "") or "").strip()
    first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "no answer from az")
    login = f"az login --tenant {tenant} --scope {AZURE_SIGNING_SCOPE}" if tenant else f"az login --scope {AZURE_SIGNING_SCOPE}"
    mfa = any(code in err for code in ("AADSTS50076", "AADSTS50079", "AADSTS50158")) or "multi-factor" in err.lower()
    why = "its multi-factor sign-in (MFA) is not finished" if mfa else "the sign-in is incomplete or expired"
    r.miss(
        f"Azure CLI is signed in as {who} but cannot get a code-signing token for tenant {tenant or '?'}: {why}. "
        f"Run: {login}   (az said: {first[:240]})"
    )
    return ""


def _az_json(az: str, args: list[str]) -> tuple[object, str]:
    """Run a read-only az command; (parsed JSON, "") or (None, what az said)."""
    proc = _run_quiet([az, *args, "-o", "json"], timeout=120)
    if proc is None:
        return None, "no answer from az"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "").strip()
    try:
        return json.loads(proc.stdout or "null"), ""
    except ValueError:
        return None, "az gave an answer that is not JSON"


def _check_az_account(r: _Report, az: str, who: str) -> bool:
    """Look the account, its certificate profile and the signer role up in Azure
    (read-only), so a missing profile shows here instead of failing the build's
    first signature. Returns True when the role was checked."""
    account, profile = _env("AZURE_TRUSTED_SIGNING_ACCOUNT"), _env("AZURE_CERT_PROFILE_NAME")
    if not (account and profile):
        return False
    found, err = _az_json(
        az,
        ["resource", "list", "--resource-type", "Microsoft.CodeSigning/codeSigningAccounts",
         "--query", "[].{id:id, name:name, location:location}"],
    )
    if not isinstance(found, list):
        r.note(f"could not look the Artifact Signing account up in Azure ({(err or 'no answer')[:200]})")
        return False
    match = [a for a in found if isinstance(a, dict) and str(a.get("name") or "").lower() == account.lower()]
    if not match:
        names = ", ".join(sorted(str(a.get("name")) for a in found if isinstance(a, dict))) or "none"
        r.miss(
            f"no Artifact Signing account named {account} in the subscription az is using (accounts there: {names}). "
            "Fix AZURE_TRUSTED_SIGNING_ACCOUNT, or switch subscription: az account set -s <subscription>"
        )
        return False
    acc = match[0]
    acc_id, location = str(acc.get("id") or ""), str(acc.get("location") or "")
    code = AZURE_REGION_CODES.get(location.lower().replace(" ", ""))
    endpoint = _env("AZURE_TRUSTED_SIGNING_ENDPOINT").rstrip("/").lower()
    if code and endpoint and endpoint != f"https://{code}.codesigning.azure.net":
        r.miss(
            f"AZURE_TRUSTED_SIGNING_ENDPOINT must be https://{code}.codesigning.azure.net: the account {account} "
            f"is in {location}, and the wrong region fails with 403"
        )
    prof, err = _az_json(
        az,
        ["rest", "--method", "get",
         "--url", f"{_ARM}{acc_id}/certificateProfiles/{profile}?api-version={_CODESIGNING_API}",
         "--query", "{type:properties.profileType, status:properties.status}"],
    )
    if isinstance(prof, dict):
        status = str(prof.get("status") or "")
        if status and status.lower() != "active":
            r.miss(f"certificate profile {profile} is {status}, not Active, so it cannot sign yet")
        else:
            r.ok(f"certificate profile {profile} ({prof.get('type') or 'profile'}) is in account {account} ({location})")
    elif "notfound" in err.lower().replace(" ", "") or "404" in err:
        r.miss(
            f"the account {account} has no certificate profile named {profile}. Create it: Azure portal > "
            f"Artifact Signing Accounts > {account} > Certificate profiles > Create > Public Trust, name it {profile} "
            "and pick your identity validation under Verified CN and O (or put an existing profile's name in "
            "AZURE_CERT_PROFILE_NAME)"
        )
    else:
        r.note(f"could not look the certificate profile up in Azure ({(err or 'no answer')[:200]})")
    holders, err = _az_json(
        az,
        ["role", "assignment", "list", "--scope", acc_id, "--include-inherited",
         "--query", f"[?roleDefinitionName=='{AZURE_SIGNER_ROLE}'].principalName"],
    )
    if not isinstance(holders, list):
        return False
    me = who.lower()
    guest = me.replace("@", "_") + "#ext#"
    if any(str(p).lower() == me or str(p).lower().startswith(guest) for p in holders):
        r.ok(f'{who} has the role "{AZURE_SIGNER_ROLE}" on {account}')
    elif holders:
        r.note(
            f'the role "{AZURE_SIGNER_ROLE}" on {account} is held by {", ".join(map(str, holders))[:200]}; '
            f"signing works if that includes {who} (a group counts)"
        )
    else:
        r.miss(
            f'nobody has the role "{AZURE_SIGNER_ROLE}" on {account}: Azure portal > Artifact Signing Accounts > '
            f"{account} > Access control (IAM) > Add role assignment > {AZURE_SIGNER_ROLE} > add yourself"
        )
    return True


def _check_pfx(r: _Report) -> None:
    pfx = Path(_env("DUCKY_WINDOWS_PFX"))
    password = os.environ.get("DUCKY_WINDOWS_PFX_PASSWORD")
    if not _env("DUCKY_WINDOWS_PFX"):
        r.miss("DUCKY_WINDOWS_PFX is blank (path to your .pfx certificate file)")
        return
    if not pfx.is_file():
        r.miss(f"DUCKY_WINDOWS_PFX file not found: {pfx}")
        return
    if password is None:
        r.miss("DUCKY_WINDOWS_PFX_PASSWORD is not set")
        return
    info = _probe_pfx(pfx, password)
    if "error" in info:
        r.miss(f"cannot open {pfx.name} with DUCKY_WINDOWS_PFX_PASSWORD: {info['error']}")
        return
    r.ok(f"certificate {info['subject']} (thumbprint {info['thumbprint']}, expires {info['notAfter']} UTC)")
    if info["selfSigned"]:
        r.note("this certificate is self-signed: it signs, but Windows and SmartScreen will not trust it")
    if not info["hasPrivateKey"]:
        r.miss(f"{pfx.name} has no private key, so it cannot sign")
    if not info["codeSigning"]:
        r.miss(f"{pfx.name} is not a code-signing certificate")
    if info["notAfter"] < time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()):
        r.miss(f"{pfx.name} expired on {info['notAfter']}")


def check(files: list[Path] | None = None) -> list[str]:
    """Print, in plain words, what a signed release would use; return what is missing."""
    r = _Report()
    envs = [str(p) for p in dotenv_paths() if p.is_file()]
    print(f"  settings from: environment + {', '.join(envs) or '(no .env file found)'}")
    mode = signing_mode()
    if mode is None:
        r.miss(
            "signing is off, so a release would ship the plain unsigned installer. "
            "Set DUCKY_SIGN_PROVIDER=azure (or pfx) in the .env to sign."
        )
        return r.missing
    if mode not in _PROVIDERS:
        r.miss(f"DUCKY_SIGN_PROVIDER={mode} is not one of {', '.join(_PROVIDERS)}")
        return r.missing
    r.ok(f"signing with {mode}")

    signtool = find_signtool()
    if signtool:
        r.ok(f"signtool: {signtool}")
    elif _env("DUCKY_SIGNTOOL"):
        r.miss(f"DUCKY_SIGNTOOL points at a file that does not exist: {_env('DUCKY_SIGNTOOL')}")
    else:
        r.miss(
            "signtool is not installed: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools "
            "(includes signtool) or the Windows SDK (winget install -e --id Microsoft.WindowsSDK.10.0.26100)"
        )

    if mode == "azure":
        _check_azure(r, signtool)
    elif mode == "pfx":
        _check_pfx(r)
    elif extra_args():
        r.ok(f"signtool arguments: {_redacted(extra_args())}")
    else:
        r.miss("DUCKY_SIGN_PROVIDER=signtool needs DUCKY_SIGNTOOL_EXTRA (your signtool arguments)")

    url = timestamp_url()
    if url.lower().startswith(("http://", "https://")):
        r.ok(f"timestamp server: {url}")
    else:
        r.miss(f"DUCKY_SIGN_TIMESTAMP_URL is not a web address: {url}")
    if _env("DUCKY_SIGNING_PROFILE_EKU"):
        r.ok(f"every signed file must carry EKU {_env('DUCKY_SIGNING_PROFILE_EKU')}")

    iscc = find_iscc()
    if iscc:
        r.ok(f"Inno Setup: {iscc}")
    else:
        r.miss("Inno Setup 6 is not installed: winget install -e --id JRSoftware.InnoSetup (or set INNO_SETUP_ISCC)")
    host, sdks = _dotnet_sdks()
    if sdks:
        r.ok(f".NET SDK {', '.join(sdks)} ({host}) builds the Ducky Setup installer")
    else:
        r.miss(".NET 8 SDK is not installed (it builds the Ducky Setup installer): winget install -e --id Microsoft.DotNet.SDK.8")

    for path in files or []:
        info = signature_info(path)
        if not info.get("subject"):
            r.miss(f"{path.name} is not signed")
            continue
        when = "timestamped" if info.get("timestamped") else "NOT timestamped"
        print(f"  file     {path.name}: signed by {info['subject']} ({info['status']}, {when})")
        print(f"           thumbprint {info['thumbprint']}; EKUs {', '.join(info['eku']) or 'none'}")
    return r.missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="EXE paths to sign (or, with --check, to inspect)")
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail if signing is not configured (refuse an unsigned publish)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Say what is ready and what is missing to sign a release; signs nothing",
    )
    parser.add_argument("--self-check", action="store_true", help="Sanity check helpers and exit")
    args = parser.parse_args()

    if args.self_check:
        assert DEFAULT_TIMESTAMP.startswith("http")
        print("sign_windows self-check ok")
        return

    _load_dotenv()

    if args.check:
        print("=== Code signing check ===")
        missing = check(args.files)
        if missing:
            print(f"Not ready: {len(missing)} thing(s) missing (above).")
        else:
            print("Ready: a release will sign the app, the setup engine and the Ducky Setup installer.")
        raise SystemExit(2 if not signing_configured() else (1 if missing else 0))

    if not args.files:
        raise SystemExit("Pass one or more EXE paths to sign (or --check / --self-check)")

    if not signing_configured():
        msg = (
            "No Windows code signing configured (set DUCKY_SIGN_PROVIDER=azure with the "
            "AZURE_TRUSTED_SIGNING_* keys, or DUCKY_WINDOWS_PFX + DUCKY_WINDOWS_PFX_PASSWORD). "
            "Chrome/SmartScreen will keep warning on Setup.exe until releases are signed."
        )
        if args.require:
            print(msg, file=sys.stderr)
            raise SystemExit(2)
        print(f"  warn: {msg}")
        return

    signtool = find_signtool()
    if signtool is None:
        raise SystemExit(
            "signtool.exe not found. Install the Windows SDK signing tools "
            "(winget install -e --id Microsoft.WindowsSDK.10.0.26100), or set DUCKY_SIGNTOOL."
        )

    for path in args.files:
        print(f"=== Sign {path.name} ===")
        sign_file(path, signtool=signtool)
        verify_file(path, signtool=signtool)
        print(f"  ok: {path.name}")


if __name__ == "__main__":
    main()
