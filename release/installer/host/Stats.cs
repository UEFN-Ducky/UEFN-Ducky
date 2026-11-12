using System;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace DuckySetup
{
    // The pure rules behind the anonymous counts Setup and the uninstaller send: which
    // run this is, buckets, codes and the uninstall answer. No registry, network or
    // WinForms, so host.tests compiles it on its own. The Store checks every name and
    // value again (plugin-uefn-ducky-store src/usage.rs) and drops anything else.
    internal static class Stats
    {
        // Why someone uninstalls, in the order the uninstaller lists them.
        public static readonly string[] Reasons =
        {
            "reinstall", "not_using", "setup_hard", "ai_trouble", "uefn_trouble", "bugs", "slow", "missing", "other",
        };

        // The most the uninstaller's "tell us more" box sends.
        public const int TextMax = 500;

        static readonly string[] Screens = { "welcome", "options", "license", "progress", "error", "done" };

        // new: this PC never had UEFN Ducky; reinstall: it had (its id is still here)
        // but no install is; update: Setup run over an install.
        public static string Kind(bool installed, bool hadId) => installed ? "update" : hadId ? "reinstall" : "new";

        public static bool IsInstallId(string s) =>
            s.Length == 32 && s.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));

        public static string NewInstallId(byte[] random16)
        {
            var sb = new StringBuilder(32);
            foreach (var b in random16.Take(16))
                sb.Append(b.ToString("x2"));
            return sb.ToString();
        }

        // How long the PC had UEFN Ducky (unknown when its id has no date).
        public static string AgeBucket(long seconds)
        {
            if (seconds < 0)
                return "unknown";
            if (seconds <= 86_400)
                return "day0";
            if (seconds <= 7 * 86_400)
                return "week1";
            if (seconds <= 30 * 86_400)
                return "month1";
            return "older";
        }

        // How long an install took, from Install to done.
        public static string TookBucket(double seconds)
        {
            if (seconds <= 60)
                return "1m";
            if (seconds <= 300)
                return "5m";
            if (seconds <= 1200)
                return "20m";
            return "more";
        }

        // An engine or uninstaller exit code as an error code ("ok" for 0). A negative code is
        // Windows ending the process: exit_killed (ended from outside: Ctrl+C, sign-out, shutdown,
        // End task), exit_crashed (it faulted), exit_neg for any other. "exit_neg" alone (a failed
        // web update on Nov 10 2026) could not tell a kill from a crash.
        public static string ExitCode(int code)
        {
            if (code >= 0)
                return code == 0 ? "ok" : "exit_" + code;
            switch (unchecked((uint)code))
            {
                case 0xC000013A: // STATUS_CONTROL_C_EXIT
                case 0x40010004: // DBG_TERMINATE_PROCESS
                    return "exit_killed";
                case 0xC0000005: // access violation
                case 0xC0000409: // stack buffer overrun / fail fast
                case 0xC000001D: // illegal instruction
                case 0xC00000FD: // stack overflow
                    return "exit_crashed";
                default:
                    return "exit_neg";
            }
        }

        // Why the engine failed, from the end of its Inno Setup log ("" when it doesn't say).
        // exit_4 alone ("a fatal error during installation", 1 of 5 installs on Oct 28 2026)
        // could not tell a locked file from no permission or a full disk.
        public static string FailReason(string? log)
        {
            var t = (log ?? "").ToLowerInvariant();
            if (t.Contains("being used by another process") || t.Contains("code 32.") || t.Contains("restartmanager found an application")
                || Regex.IsMatch(t, @"\berror 32\b"))
                return "file_in_use";
            if (t.Contains("not enough space") || t.Contains("code 112."))
                return "disk_full";
            if (t.Contains("access is denied") || t.Contains("code 5.") || Regex.IsMatch(t, @"\berror 5\b"))
                return "access_denied";
            if (t.Contains("setup was not completed"))
                return "inno_not_completed";
            return "";
        }

        // The log's last line, as "unknown_<text>", when FailReason knows none of it: a bare
        // exit_4 told us nothing. 32 characters, so exit_4.unknown_... stays within 48.
        public static string UnknownReason(string? log)
        {
            var line = (log ?? "").Replace("\r", "").Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.Length > 0) ?? "";
            // Inno log lines start with a timestamp: "2026-11-06 10:12:33.120   ...".
            var text = Regex.Replace(Regex.Replace(line.ToLowerInvariant(), @"^[\d\-:. ]+", ""), @"[^a-z0-9]+", "_").Trim('_');
            return text.Length == 0 ? "unknown" : "unknown_" + (text.Length > 32 ? text.Substring(0, 32).TrimEnd('_') : text);
        }

        // Downloads paused on the Store: check again every minute for 10 minutes.
        public const int PauseChecks = 10;
        public const int PauseCheckSeconds = 60;

        // The progress line while waiting: the Store paused downloads (a release going out).
        public static string PausedStatus(int check) =>
            "Waiting for the UEFN Ducky Store… downloads are paused, checking again in a minute ("
            + check + " of " + PauseChecks + ").";

        // The first answer that is no longer paused, or the last paused one after PauseChecks.
        // A failed recheck keeps waiting; a cancel (OperationCanceledException from wait) stops.
        public static T WaitWhilePaused<T>(T first, Func<T, bool> paused, Func<T> refetch, Action<int> wait, Action<int>? onCheck = null)
        {
            var current = first;
            for (var check = 1; check <= PauseChecks && paused(current); check++)
            {
                onCheck?.Invoke(check);
                wait(PauseCheckSeconds);
                try { current = refetch(); }
                catch (Exception ex) when (!(ex is OperationCanceledException)) { /* Store still down: next check */ }
            }
            return current;
        }

        // Microsoft Edge WebView2 Runtime is not on the PC (removed with Edge by a "debloat" tool,
        // or never installed): Setup's window and the app's both need it.
        public const string WebView2Page = "https://developer.microsoft.com/microsoft-edge/webview2/";
        public const string WebView2MissingText =
            "UEFN Ducky needs Microsoft Edge WebView2 Runtime to show its window, and this PC doesn't have it. "
            + "Tools that remove Microsoft Edge take it out too.\n\n"
            + "Get the Evergreen installer from Microsoft: " + WebView2Page + "\n"
            + "Install it, then run this Setup again.\n\n"
            + "Open Microsoft's download page now?";

        // True for the error WebView2 raises when no runtime is installed (type name or its text).
        public static bool IsWebView2Missing(string? error)
        {
            var t = (error ?? "").ToLowerInvariant();
            return t.Contains("webview2runtimenotfoundexception") || t.Contains("compatible webview2 runtime");
        }

        // Why the downloaded Setup itself gave up before or around its engine, from its setup-host.log.
        // exit_1 alone (2 of 10 new web installs on Oct 29 2026) could be any of these.
        public static string HostFailReason(string? hostLog)
        {
            var t = (hostLog ?? "").ToLowerInvariant();
            if (t.Length == 0)
                return "";
            if (IsWebView2Missing(t))
                return "webview2_missing";
            if (t.Contains("virus") || t.Contains("potentially unwanted"))
                return "antivirus";
            if (t.Contains("web installer, not the full setup"))
                return "web_child";
            var engine = FailReason(t);
            if (engine.Length > 0)
                return engine;
            if (t.Contains("unauthorizedaccessexception"))
                return "access_denied";
            if (t.Contains("could not start"))
                return "engine_start";
            return "host_error";
        }

        // exit_1 with neither log saying why: the engine never wrote its log (no_log) or it did
        // and named nothing; otherwise whatever the downloaded Setup's own log says.
        public static string ExitOneReason(string engineLog, string hostLog)
        {
            var host = HostFailReason(hostLog);
            if (host.Length > 0)
                return host;
            return engineLog.Trim().Length == 0 ? "no_log" : "";
        }

        // exit_4 plus its reason when the log names one: exit_4.file_in_use.
        public static string FailCode(int code, string reason) => ExitCode(code) + (code != 0 && reason.Length > 0 ? "." + reason : "");

        public static string Outcome(bool ok, bool cancelled) => ok ? "ok" : cancelled ? "cancel" : "fail";

        public static string Reason(string? reason)
        {
            var r = (reason ?? "").Trim();
            return Array.IndexOf(Reasons, r) >= 0 ? r : "none";
        }

        public static string Screen(string? screen)
        {
            var s = (screen ?? "").Trim();
            return Array.IndexOf(Screens, s) >= 0 ? s : "welcome";
        }

        // An install started from the error screen is a Try again (setup.step step:retry).
        public static bool IsRetry(string? screenBefore) => screenBefore == "error";

        public static string Os(int build) => build >= 22000 ? "win11" : "win10";

        // The PC's default browser, from the ProgId that opens https links: most likely the one
        // that downloaded Setup. Brave reads as chrome, as it does on the site's Download click.
        public static string Browser(string? progId)
        {
            var p = (progId ?? "").Trim().ToLowerInvariant();
            if (p.StartsWith("msedge", StringComparison.Ordinal)) return "edge";
            if (p.StartsWith("chrome", StringComparison.Ordinal) || p.StartsWith("brave", StringComparison.Ordinal)) return "chrome";
            if (p.StartsWith("firefox", StringComparison.Ordinal)) return "firefox";
            if (p.StartsWith("opera", StringComparison.Ordinal)) return "opera";
            return "other";
        }

        // ---- "Open UEFN Ducky": how the app's first start went, read from the app's own boot
        // record (LaunchCheck.cs). The Store's words for it are setup.launch in usage.rs.

        // Setup watches this long after the press, with no window.
        public const int LaunchWatchSeconds = 90;
        // A failure counts once it held this long: a fast-launcher start that fails is followed
        // by the Python window's own start, which writes a new record.
        public const int LaunchSettleSeconds = 15;

        static readonly string[] BootStages =
        {
            "launch", "handoff", "handoff_exit", "reclaim_claim", "webview_import_patches", "import_panel_api",
            "panel_api_init", "http_server", "create_window", "ready_for_webview_start", "window_shown", "page_started",
        };

        // The app's last boot stage as the Store lists it ("other" for a name it doesn't).
        public static string Stage(string? name)
        {
            var s = (name ?? "").Trim();
            return Array.IndexOf(BootStages, s) >= 0 ? s : "other";
        }

        // One look at the boot record: "started", a failure word, or "" (still starting).
        // fresh: the record is this start's; alive: the process that wrote it still runs.
        public static string LaunchLook(bool fresh, string? state, bool alive)
        {
            if (!fresh)
                return "";
            switch ((state ?? "").Trim())
            {
                case "ok": return "started";
                case "fatal": return "error";
                case "stuck": return "stuck";
                case "closed":
                case "stopped": return "closed";
                case "handoff": return ""; // another start took over and writes its own record
                default: return alive ? "" : "died";
            }
        }

        // What to send, or "" to look again: started at once; a failure once it held (or time is
        // up); with time up and nothing final, slow (still starting) or never_appeared (no record).
        public static string LaunchVerdict(string look, double heldSeconds, bool fresh, bool timeUp)
        {
            if (look == "started")
                return look;
            if (look.Length > 0 && (heldSeconds >= LaunchSettleSeconds || timeUp))
                return look;
            if (!timeUp)
                return "";
            return fresh ? "slow" : "never_appeared";
        }

        // How long the app took to come up after the press.
        public static string LaunchTook(double seconds) => seconds <= 5 ? "5s" : seconds <= 15 ? "15s" : seconds <= 60 ? "60s" : "more";

        // The program a registry command line runs: "C:\a b\x.exe" /SILENT is C:\a b\x.exe.
        public static string ExePath(string? command)
        {
            var c = (command ?? "").Trim();
            if (c.StartsWith("\""))
            {
                var end = c.IndexOf('"', 1);
                return end > 1 ? c.Substring(1, end - 1) : "";
            }
            var space = c.IndexOf(' ');
            return space < 0 ? c : c.Substring(0, space);
        }

        // What someone typed in the uninstaller: trimmed, no control characters
        // (line breaks stay), at most TextMax characters.
        public static string CleanText(string? text)
        {
            var sb = new StringBuilder();
            foreach (var c in (text ?? "").Replace("\r\n", "\n").Trim())
            {
                if (c == '\n' || !char.IsControl(c))
                    sb.Append(c);
                if (sb.Length >= TextMax)
                    break;
            }
            return sb.ToString().Trim();
        }
    }
}
