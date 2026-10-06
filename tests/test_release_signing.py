"""Signed releases: how signing is configured, what gets signed, when the custom
Setup host ships, and that GitHub Actions runs the same release a local one does."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RELEASE = REPO / "release"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", RELEASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sw(monkeypatch, tmp_path):
    """sign_windows with no signing settings and no real tool folders in reach."""
    mod = _load("sign_windows")
    for key in (*mod.SIGNING_KEYS, "AZURE_CLIENT_CERTIFICATE_PATH", "DUCKY_SIGN_PYTHON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("NUGET_PACKAGES", str(tmp_path / "nuget"))
    return mod


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


# --- settings -------------------------------------------------------------


def test_keylocker_extra_args_keep_quoted_words_and_windows_paths(sw, monkeypatch):
    monkeypatch.setenv(
        "DUCKY_SIGNTOOL_EXTRA",
        r'/csp "DigiCert Signing Manager KSP" /kc key_123 /f "C:\Certs\My Cert.crt"',
    )
    assert sw.extra_args() == ["/csp", "DigiCert Signing Manager KSP", "/kc", "key_123", "/f", r"C:\Certs\My Cert.crt"]


def test_dotenv_strips_only_a_matching_outer_quote_pair(sw, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'DUCKY_WINDOWS_PFX="C:\\certs\\ducky.pfx"\n'
        "DUCKY_WINDOWS_PFX_PASSWORD='p=a#ss'\n"
        'DUCKY_SIGNTOOL_EXTRA=/csp "DigiCert Signing Manager KSP" /f "C:\\a b\\c.crt"\n',
        encoding="utf-8",
    )
    sw.load_dotenv([env])
    assert os.environ["DUCKY_WINDOWS_PFX"] == r"C:\certs\ducky.pfx"
    assert os.environ["DUCKY_WINDOWS_PFX_PASSWORD"] == "p=a#ss"
    assert sw.extra_args()[-1] == r"C:\a b\c.crt"


def test_dotenv_keys_filter_reads_only_those_keys(sw, tmp_path, monkeypatch):
    monkeypatch.delenv("DUCKYOS_SIGNING_TEST_OTHER", raising=False)
    env = tmp_path / ".env"
    env.write_text("DUCKY_SIGN_PROVIDER=azure\nDUCKYOS_SIGNING_TEST_OTHER=other-site\n", encoding="utf-8")
    sw.load_dotenv([env], keys=sw.SIGNING_KEYS)
    assert os.environ["DUCKY_SIGN_PROVIDER"] == "azure"
    assert "DUCKYOS_SIGNING_TEST_OTHER" not in os.environ


def test_the_owners_env_template_signs_with_azure_and_blank_keys_stay_unset(sw, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DUCKY_SIGN_PROVIDER=azure\nDUCKY_AZURE_AUTH=cli\n"
        "AZURE_TRUSTED_SIGNING_ENDPOINT=https://eus.codesigning.azure.net\n"
        "AZURE_TRUSTED_SIGNING_ACCOUNT=ducky\nAZURE_CERT_PROFILE_NAME=ducky-public\n"
        "AZURE_CLIENT_ID=\nAZURE_TENANT_ID=\nAZURE_CLIENT_SECRET=\nAZURE_SUBSCRIPTION_ID=\n"
        "DUCKY_SIGNING_PROFILE_EKU=\nDUCKY_AZURE_DLIB=\nDUCKY_SIGNTOOL=\nINNO_SETUP_ISCC=\n",
        encoding="utf-8",
    )
    sw.load_dotenv([env])
    assert sw.signing_mode() == "azure"
    for blank in ("AZURE_CLIENT_ID", "DUCKY_AZURE_DLIB", "DUCKY_SIGNTOOL", "INNO_SETUP_ISCC", "DUCKY_SIGNING_PROFILE_EKU"):
        assert blank not in os.environ
    assert sw.timestamp_url() == "http://timestamp.acs.microsoft.com"


def test_provider_picks_the_signing_and_commenting_it_out_means_unsigned(sw, monkeypatch):
    assert sw.signing_mode() is None
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "ducky")  # filled in, provider commented out
    assert sw.signing_mode() is None and not sw.signing_configured()
    monkeypatch.setenv("DUCKY_WINDOWS_PFX", r"C:\c.pfx")
    assert sw.signing_mode() == "pfx"  # older .env files without a provider
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "Azure")
    assert sw.signing_mode() == "azure"
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "off")
    assert sw.signing_mode() is None
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azur")  # a typo fails loudly, never unsigned
    assert sw.signing_configured()
    with pytest.raises(SystemExit):
        sw._credential_args(sw.signing_mode(), Path("."))


@pytest.mark.parametrize(
    ("auth", "kept"),
    [("cli", "AzureCliCredential"), ("environment", "EnvironmentCredential"), ("", None)],
)
def test_azure_auth_keeps_one_sign_in_kind(sw, monkeypatch, auth, kept):
    monkeypatch.setenv("DUCKY_AZURE_AUTH", auth)
    meta = sw.azure_metadata()
    if kept is None:
        assert "ExcludeCredentials" not in meta
    else:
        assert kept not in meta["ExcludeCredentials"]
        assert set(meta["ExcludeCredentials"]) | {kept} == set(sw._AZURE_CREDENTIALS)


def test_endpoint_must_be_an_artifact_signing_region(sw):
    for good in ("https://eus.codesigning.azure.net", "https://wus2.codesigning.azure.net/", "https://WEU.codesigning.azure.net"):
        assert sw.AZURE_ENDPOINT_RE.match(good)
    for bad in ("https://codesigning.azure.net", "http://eus.codesigning.azure.net", "https://xx.codesigning.azure.net", "eus"):
        assert not sw.AZURE_ENDPOINT_RE.match(bad)


# --- tools found automatically ---------------------------------------------


def test_blank_dlib_and_signtool_are_found_in_the_client_tools_and_nuget(sw, tmp_path, monkeypatch):
    monkeypatch.setattr(sw.shutil, "which", lambda _name: None)
    tools = tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools"
    _touch(tools / "bin" / "x86" / "Azure.CodeSigning.Dlib.dll")
    x64 = _touch(tools / "bin" / "x64" / "Azure.CodeSigning.Dlib.dll")
    assert sw.find_azure_dlib() == x64
    old = _touch(tmp_path / "nuget" / "microsoft.windows.sdk.buildtools" / "10.0.22621.755" / "bin" / "10.0.22621.0" / "x64" / "signtool.exe")
    new = _touch(tmp_path / "nuget" / "microsoft.windows.sdk.buildtools" / "10.0.26100.1" / "bin" / "10.0.26100.0" / "x64" / "signtool.exe")
    assert sw.find_signtool() == new != old
    monkeypatch.setenv("DUCKY_SIGNTOOL", str(old))
    assert sw.find_signtool() == old


def test_old_trusted_signing_nuget_package_is_found_too(sw, tmp_path):
    dlib = _touch(tmp_path / "nuget" / "microsoft.trusted.signing.client" / "1.0.95" / "bin" / "x64" / "Azure.CodeSigning.Dlib.dll")
    assert sw.find_azure_dlib() == dlib


# --- signing ----------------------------------------------------------------


def test_azure_sign_command_uses_the_dlib_metadata_and_microsoft_timestamp(sw, tmp_path, monkeypatch):
    dlib = _touch(tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools" / "bin" / "x64" / "Azure.CodeSigning.Dlib.dll")
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("DUCKY_AZURE_AUTH", "cli")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://eus.codesigning.azure.net")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "ducky")
    monkeypatch.setenv("AZURE_CERT_PROFILE_NAME", "ducky-public")
    exe = _touch(tmp_path / "app.exe")
    seen: dict = {}

    def run(cmd, **_kwargs):
        seen["cmd"] = cmd
        seen["meta"] = json.loads(Path(cmd[cmd.index("/dmdf") + 1]).read_text(encoding="utf-8"))

    monkeypatch.setattr(sw.subprocess, "run", run)
    sw.sign_file(exe, signtool=Path("signtool.exe"))
    cmd = seen["cmd"]
    assert cmd[:3] == ["signtool.exe", "sign", "/v"]
    assert cmd[cmd.index("/tr") + 1] == "http://timestamp.acs.microsoft.com"
    assert cmd[cmd.index("/dlib") + 1] == str(dlib)
    assert cmd[-1] == str(exe)
    assert seen["meta"]["CodeSigningAccountName"] == "ducky"
    assert seen["meta"]["Endpoint"] == "https://eus.codesigning.azure.net"
    assert "AzureCliCredential" not in seen["meta"]["ExcludeCredentials"]


def test_azure_with_a_blank_account_stops_with_the_key_to_fill(sw, monkeypatch):
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    with pytest.raises(SystemExit) as exc:
        sw._credential_args("azure", Path("."))
    assert "AZURE_TRUSTED_SIGNING_ACCOUNT" in str(exc.value)


def test_profile_eku_is_enforced_on_every_signed_file(sw, monkeypatch, tmp_path):
    monkeypatch.setenv("DUCKY_SIGNING_PROFILE_EKU", "1.3.6.1.4.1.311.97.1")
    monkeypatch.setattr(sw.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(sw, "signature_info", lambda _p: {"subject": "CN=Other", "eku": ["1.3.6.1.5.5.7.3.3"]})
    with pytest.raises(SystemExit):
        sw.verify_file(tmp_path / "a.exe", signtool=Path("signtool.exe"))
    monkeypatch.setattr(sw, "signature_info", lambda _p: {"subject": "CN=Ducky", "eku": ["1.3.6.1.5.5.7.3.3", "1.3.6.1.4.1.311.97.1"]})
    sw.verify_file(tmp_path / "a.exe", signtool=Path("signtool.exe"))


# --- --check ------------------------------------------------------------------


def _ready_tools(sw, monkeypatch, tmp_path, *, az: str | None):
    monkeypatch.setattr(sw, "find_signtool", lambda: tmp_path / "bin" / "x64" / "signtool.exe")
    monkeypatch.setattr(sw, "_file_version", lambda _p: (10, 0, 26100, 1))
    monkeypatch.setattr(sw, "find_iscc", lambda: tmp_path / "ISCC.exe")
    monkeypatch.setattr(sw, "_dotnet_sdks", lambda: (tmp_path / "dotnet.exe", ["8.0.425"]))
    monkeypatch.setattr(sw.shutil, "which", lambda name: az if name == "az" else None)

    class Done:
        returncode = 0
        stdout = "owner@example.com\n"

    monkeypatch.setattr(sw, "_run_quiet", lambda *_a, **_k: Done())


def test_check_says_in_plain_words_what_azure_is_missing(sw, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("DUCKY_AZURE_AUTH", "cli")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://example.com")
    _ready_tools(sw, monkeypatch, tmp_path, az=None)
    missing = sw.check()
    text = "\n".join(missing)
    assert "AZURE_TRUSTED_SIGNING_ACCOUNT is blank" in text
    assert "AZURE_CERT_PROFILE_NAME is blank" in text
    assert "not an Artifact Signing endpoint" in text
    assert "winget install -e --id Microsoft.Azure.ArtifactSigningClientTools" in text
    assert "Azure CLI is not installed" in text
    out = capsys.readouterr().out
    assert "ready    signing with azure" in out
    assert "Artifact Signing Certificate Profile Signer" in out


def test_check_is_ready_when_azure_is_filled_in_and_signed_in(sw, monkeypatch, tmp_path):
    _touch(tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools" / "bin" / "x64" / "Azure.CodeSigning.Dlib.dll")
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("DUCKY_AZURE_AUTH", "cli")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://eus.codesigning.azure.net")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "ducky")
    monkeypatch.setenv("AZURE_CERT_PROFILE_NAME", "ducky-public")
    _ready_tools(sw, monkeypatch, tmp_path, az="az.cmd")
    assert sw.check() == []


def test_check_rejects_a_signtool_too_old_for_the_dlib(sw, monkeypatch, tmp_path):
    _touch(tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools" / "bin" / "x64" / "Azure.CodeSigning.Dlib.dll")
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://eus.codesigning.azure.net")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "ducky")
    monkeypatch.setenv("AZURE_CERT_PROFILE_NAME", "ducky-public")
    _ready_tools(sw, monkeypatch, tmp_path, az="az.cmd")
    monkeypatch.setattr(sw, "_file_version", lambda _p: (10, 0, 20348, 1))
    assert any("too old" in m for m in sw.check())


def test_check_with_signing_off_says_so(sw):
    missing = sw.check()
    assert len(missing) == 1 and "signing is off" in missing[0]


# --- the release build (publish_app.build_setup) -----------------------------


@pytest.fixture
def pub(sw, monkeypatch):
    monkeypatch.syspath_prepend(str(RELEASE))
    sys.modules.pop("sign_windows", None)
    return _load("publish_app")


def _fake_build(pub, tmp_path, monkeypatch):
    """Stand in for build_exes / ISCC / dotnet; record every command."""
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    monkeypatch.setattr(pub, "read_version", lambda: "1.2.3")
    app = tmp_path / "dist" / "UEFN-Ducky-1.2.3"
    _touch(app / "UEFN-Ducky.exe")
    _touch(app / "UEFN-Ducky-Bridge.exe")
    calls: list[tuple[list[str], dict | None]] = []

    def run(cmd, *args, **kwargs):
        cmd = [str(c) for c in cmd]
        calls.append((cmd, kwargs.get("env")))
        if "-EngineOnly" in cmd:
            (tmp_path / "dist" / "Setup-engine.exe").write_bytes(b"MZ inno engine")
        if "-HostOnly" in cmd:
            (tmp_path / "dist" / "UEFN-Ducky-Setup-1.2.3.exe").write_bytes(b"MZ ducky host")

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(pub.subprocess, "run", run)
    return calls


def _signed(calls):
    return [c for c, _ in calls if c[1].endswith("sign_windows.py")]


@pytest.mark.parametrize("provider", ["azure", "pfx"])
def test_signed_build_signs_app_exes_engine_and_ships_custom_host(pub, tmp_path, monkeypatch, provider):
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", provider)
    calls = _fake_build(pub, tmp_path, monkeypatch)

    assert pub.build_setup(require_sign=True, bump=False) == "1.2.3"

    signs = _signed(calls)
    app = tmp_path / "dist" / "UEFN-Ducky-1.2.3"
    assert signs[0][2:] == ["--require", str(app / "UEFN-Ducky-Bridge.exe"), str(app / "UEFN-Ducky.exe")]
    assert signs[1][-1] == str(tmp_path / "dist" / "Setup-engine.exe")
    assert signs[2][-1] == str(tmp_path / "dist" / "UEFN-Ducky-Setup-1.2.3.exe")
    assert all("--require" in s for s in signs)
    # Engine built and signed before the host embeds it; the host is signed last.
    order = [c for c, _ in calls if "-EngineOnly" in c or "-HostOnly" in c or c[1].endswith("sign_windows.py")]
    assert "-EngineOnly" in order[1] and order[2][-1].endswith("Setup-engine.exe") and "-HostOnly" in order[3]
    engine_env = next(env for c, env in calls if "-EngineOnly" in c)
    assert engine_env and engine_env.get("DUCKY_SIGN_PYTHON") == sys.executable  # ISCC signs the uninstaller
    assert (tmp_path / "dist" / "UEFN-Ducky-Setup-1.2.3.exe").read_bytes() == b"MZ ducky host"


def test_unsigned_build_ships_the_inno_stub_never_the_custom_host(pub, tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_SIGN_PYTHON", "stale-from-a-parent-shell")
    calls = _fake_build(pub, tmp_path, monkeypatch)

    pub.build_setup(require_sign=False, bump=False)

    assert not any("-HostOnly" in c for c, _ in calls)
    engine_env = next(env for c, env in calls if "-EngineOnly" in c)
    assert "DUCKY_SIGN_PYTHON" not in engine_env
    assert (tmp_path / "dist" / "UEFN-Ducky-Setup-1.2.3.exe").read_bytes() == b"MZ inno engine"


def test_missing_app_build_output_stops_the_release(pub, tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        pub.payload_exes("9.9.9")


def _no_tests(pub, monkeypatch) -> list[str]:
    import sign_windows

    monkeypatch.setattr(sign_windows, "load_dotenv", lambda paths, keys=None: None)
    ran: list[str] = []
    monkeypatch.setattr(pub, "run_security_gate", lambda: ran.append("gate"))
    monkeypatch.setattr(pub, "run_regression_tests", lambda: ran.append("tests"))
    return ran


def test_require_sign_without_a_certificate_stops_before_tests(pub, monkeypatch):
    ran = _no_tests(pub, monkeypatch)
    monkeypatch.setattr(pub.sys, "argv", ["publish_app.py", "--build-only", "--require-sign"])
    with pytest.raises(SystemExit) as exc:
        pub.main()
    assert "no code-signing certificate" in str(exc.value)
    assert ran == []


def test_signing_on_but_not_ready_stops_before_tests(pub, monkeypatch):
    ran = _no_tests(pub, monkeypatch)
    import sign_windows

    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setattr(sign_windows, "check", lambda files=None: ["AZURE_TRUSTED_SIGNING_ACCOUNT is blank."])
    monkeypatch.setattr(pub.sys, "argv", ["publish_app.py", "--phase", "test"])
    with pytest.raises(SystemExit) as exc:
        pub.main()
    assert "AZURE_TRUSTED_SIGNING_ACCOUNT is blank" in str(exc.value)
    assert ran == []


def test_set_version_goes_with_the_build_step_only(pub, monkeypatch):
    _no_tests(pub, monkeypatch)
    monkeypatch.setattr(pub.sys, "argv", ["publish_app.py", "--phase", "upload", "--set-version", "1.3.0"])
    with pytest.raises(SystemExit) as exc:
        pub.main()
    assert "--phase build" in str(exc.value)


# --- GitHub Actions runs the same release -------------------------------------


def test_github_release_runs_the_same_publish_app_steps_as_a_local_release():
    pub = _load("publish_app")
    yml = (REPO / ".github" / "workflows" / "store-publish.yml").read_text(encoding="utf-8")
    calls = []
    for line in yml.splitlines():
        m = re.search(r"release/publish_app\.py(.*)$", line)
        if m and not line.lstrip().startswith("#"):
            args = [a for a in shlex.split(m.group(1)) if a != "${extra[@]}"]
            calls.append(" ".join(args))
    assert calls == list(pub.RELEASE_STEPS)
    # Optional inputs only ride on the step that owns them.
    assert re.search(r'publish_app\.py --phase build "\$\{extra\[@\]\}"', yml)
    assert 'extra+=(--set-version "${SET_VERSION}")' in yml and 'extra+=(--notes "${NOTES}")' in yml
    # Signing happens inside publish_app (sign_windows.py), never as a separate action.
    for gone in ("trusted-signing-action", "artifact-signing-action", "--build-only", "--no-bump", "--exe", "signtool sign"):
        assert gone not in yml
    assert "DUCKY_SIGN_PROVIDER=azure" in yml and "DUCKY_AZURE_AUTH=cli" in yml
    assert "DUCKY_SIGN_PROVIDER=none" in yml  # no Azure secrets: unsigned, as before
    assert yml.index("azure/login@") < yml.index("--phase test")
    assert "Microsoft.ArtifactSigning.Client" in yml


def test_dlib_sitting_directly_in_the_client_tools_folder_is_found(sw, tmp_path):
    # winget's Artifact Signing client tools 2.x put the dlib at the folder's top.
    tools = tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools"
    dlib = _touch(tools / "Azure.CodeSigning.Dlib.dll")
    assert sw.find_azure_dlib() == dlib
    old = _touch(tmp_path / "local" / "Microsoft" / "MicrosoftTrustedSigningClientTools" / "x64" / "Azure.CodeSigning.Dlib.dll")
    (dlib).unlink()
    assert sw.find_azure_dlib() == old


def test_check_says_when_azure_mfa_is_still_pending(sw, monkeypatch, tmp_path):
    _touch(tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools" / "Azure.CodeSigning.Dlib.dll")
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("DUCKY_AZURE_AUTH", "cli")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://eus.codesigning.azure.net")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "uefnducky")
    monkeypatch.setenv("AZURE_CERT_PROFILE_NAME", "UEFNDuckyRelease")
    _ready_tools(sw, monkeypatch, tmp_path, az="az.cmd")

    class Proc:
        def __init__(self, code, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    def run_quiet(cmd, timeout=60):
        if "get-access-token" in cmd:
            assert "https://codesigning.azure.net/.default" in cmd and "expiresOn" in cmd
            return Proc(1, err="ERROR: AADSTS50076: you must use multi-factor authentication to access this resource.")
        return Proc(0, out="owner@example.com\n3b436341-9726-4c5b-aed7-ef84359d8b6e\n")

    monkeypatch.setattr(sw, "_run_quiet", run_quiet)
    missing = sw.check()
    assert len(missing) == 1
    assert "multi-factor sign-in (MFA) is not finished" in missing[0]
    assert "az login --tenant 3b436341-9726-4c5b-aed7-ef84359d8b6e --scope https://codesigning.azure.net/.default" in missing[0]


def _azure_lookup(sw, monkeypatch, tmp_path, *, location="eastus", profile=None, holders=()):
    """Fake az: signed in, one account, the given profile (None = not found) and role holders."""
    _touch(tmp_path / "local" / "Microsoft" / "MicrosoftArtifactSigningClientTools" / "Azure.CodeSigning.Dlib.dll")
    monkeypatch.setenv("DUCKY_SIGN_PROVIDER", "azure")
    monkeypatch.setenv("DUCKY_AZURE_AUTH", "cli")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ENDPOINT", "https://eus.codesigning.azure.net")
    monkeypatch.setenv("AZURE_TRUSTED_SIGNING_ACCOUNT", "uefnducky")
    monkeypatch.setenv("AZURE_CERT_PROFILE_NAME", "UEFNDuckyRelease")
    _ready_tools(sw, monkeypatch, tmp_path, az="az.cmd")
    acc_id = "/subscriptions/s/resourceGroups/DuckyOS/providers/Microsoft.CodeSigning/codeSigningAccounts/uefnducky"

    class Proc:
        def __init__(self, code, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    def run_quiet(cmd, timeout=60):
        if "show" in cmd:
            return Proc(0, out="owner@example.com\n3b436341-9726-4c5b-aed7-ef84359d8b6e\n")
        if "get-access-token" in cmd:
            return Proc(0, out="2026-10-06 06:00:00\n")
        if "resource" in cmd:
            return Proc(0, out=json.dumps([{"id": acc_id, "name": "UEFNDucky", "location": location}]))
        if "rest" in cmd:
            url = cmd[cmd.index("--url") + 1]
            assert url.startswith("https://management.azure.com" + acc_id + "/certificateProfiles/UEFNDuckyRelease?")
            if profile is None:
                return Proc(1, err="ERROR: Not Found({\"error\":{\"code\":\"ResourceNotFound\"}})")
            return Proc(0, out=json.dumps(profile))
        if "role" in cmd:
            assert cmd[cmd.index("--scope") + 1] == acc_id
            return Proc(0, out=json.dumps(list(holders)))
        raise AssertionError(cmd)

    monkeypatch.setattr(sw, "_run_quiet", run_quiet)


def test_check_finds_a_missing_certificate_profile_in_azure(sw, monkeypatch, tmp_path, capsys):
    _azure_lookup(sw, monkeypatch, tmp_path, holders=["owner_example.com#EXT#@owner.onmicrosoft.com"])
    missing = sw.check()
    assert len(missing) == 1
    assert "has no certificate profile named UEFNDuckyRelease" in missing[0]
    assert "Certificate profiles > Create > Public Trust" in missing[0]
    out = capsys.readouterr().out
    assert 'owner@example.com has the role "Artifact Signing Certificate Profile Signer"' in out
    assert "the signing identity needs the role" not in out


def test_check_is_ready_when_the_profile_is_active_and_the_role_is_held(sw, monkeypatch, tmp_path):
    _azure_lookup(
        sw, monkeypatch, tmp_path,
        profile={"type": "PublicTrust", "status": "Active"}, holders=["owner@example.com"],
    )
    assert sw.check() == []


def test_check_catches_the_wrong_region_a_disabled_profile_and_no_signer(sw, monkeypatch, tmp_path):
    _azure_lookup(sw, monkeypatch, tmp_path, location="westus2", profile={"type": "PublicTrust", "status": "Disabled"})
    text = "\n".join(sw.check())
    assert "must be https://wus2.codesigning.azure.net" in text
    assert "is Disabled, not Active" in text
    assert 'nobody has the role "Artifact Signing Certificate Profile Signer"' in text
