using System;
using System.Linq;
using System.Text;

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

        // An engine or uninstaller exit code as an error code ("ok" for 0).
        public static string ExitCode(int code) => code == 0 ? "ok" : code > 0 ? "exit_" + code : "exit_neg";

        // Why the engine failed, from the end of its Inno Setup log ("" when it doesn't say).
        // exit_4 alone ("a fatal error during installation", 1 of 5 installs on Oct 28 2026)
        // could not tell a locked file from no permission or a full disk.
        public static string FailReason(string? log)
        {
            var t = (log ?? "").ToLowerInvariant();
            if (t.Contains("being used by another process") || t.Contains("code 32.") || t.Contains("restartmanager found an application"))
                return "file_in_use";
            if (t.Contains("not enough space") || t.Contains("code 112."))
                return "disk_full";
            if (t.Contains("access is denied") || t.Contains("code 5."))
                return "access_denied";
            return "";
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
