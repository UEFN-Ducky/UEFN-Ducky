using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading.Tasks;

namespace DuckySetup.Tests
{
    internal static class SelfCheck
    {
        static int _failed;
        static int _passed;

        static void Check(bool ok, string what)
        {
            if (ok) { _passed++; return; }
            _failed++;
            Console.Error.WriteLine("FAIL " + what);
        }

        // Every installed updater generation (1.0.626 .. 1.2.342) passes these.
        static readonly string[] Legacy =
        {
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS",
        };

        static int Main()
        {
            Silent();
            Quoting();
            Waiting();
            WebFeed();
            WebDownload();
            StoreErrors();
            Counts();
            Console.WriteLine($"host self-check: {_passed} passed, {_failed} failed");
            return _failed == 0 ? 0 : 1;
        }

        static void Silent()
        {
            Check(HostArgs.IsSilent(Legacy.Append("/CURRENTUSER")), "updater argv (user) is silent");
            Check(HostArgs.IsSilent(Legacy.Append("/ALLUSERS")), "updater argv (machine) is silent");
            Check(HostArgs.IsSilent(new[] { "/silent" }), "/silent any case");
            Check(HostArgs.IsSilent(new[] { "/LOG", "/VerySilent" }), "/VERYSILENT anywhere");
            Check(!HostArgs.IsSilent(Array.Empty<string>()), "no args = window");
            Check(!HostArgs.IsSilent(new[] { "/SP-", "/ALLUSERS", "/VERYSILENTX", "/LOG=a.txt" }), "look-alikes are not silent");
        }

        // Inno reads its command line like Delphi ParamStr: quotes toggle, no escapes.
        static List<string> InnoParse(string cmd)
        {
            var result = new List<string>();
            var i = 0;
            while (true)
            {
                while (i < cmd.Length && cmd[i] <= ' ') i++;
                if (i >= cmd.Length) break;
                var sb = new StringBuilder();
                var quoted = false;
                while (i < cmd.Length && (quoted || cmd[i] > ' '))
                {
                    if (cmd[i] == '"') quoted = !quoted;
                    else sb.Append(cmd[i]);
                    i++;
                }
                result.Add(sb.ToString());
            }
            return result;
        }

        static void Quoting()
        {
            var user = Legacy.Append("/CURRENTUSER").ToArray();
            Check(HostArgs.Join(user) == string.Join(" ", user), "updater argv forwarded byte-for-byte");
            var cases = new[]
            {
                user,
                Legacy.Append("/ALLUSERS").Append("/LOG").ToArray(),
                new[] { "/VERYSILENT", @"/DIR=C:\Program Files\UEFN Ducky", "/TASKS=!desktopicon", "/NOLAUNCH" },
                new[] { "/VERYSILENT", @"/LOG=C:\Users\a b\AppData\Local\Temp\setup log.txt" },
                new[] { "/VERYSILENT", @"/DIR=D:\Games\UEFN Ducky\" },
            };
            foreach (var args in cases)
            {
                var parsed = InnoParse(HostArgs.Join(args));
                Check(parsed.SequenceEqual(args), "round trip: " + string.Join(" | ", args) + " -> " + string.Join(" | ", parsed));
            }
            Check(InnoParse(HostArgs.Join(new[] { @"/DIR=C:\x ""y""" })).Single() == @"/DIR=C:\x y", "embedded quotes dropped, not escaped");
        }

        sealed class FakeProbe : IEngineProbe
        {
            public DateTime Now = new DateTime(2026, 10, 6, 0, 0, 0, DateTimeKind.Utc);
            public Func<int, IReadOnlyList<EngineProc>> Engines = _ => Array.Empty<EngineProc>();
            public Func<EngineProc, int?> Exit = _ => 0;
            public int Polls;
            public List<int> Waited = new List<int>();

            public DateTime UtcNow => Now;
            public void Sleep(TimeSpan delay) => Now += delay;

            public IReadOnlyList<EngineProc> OtherEngines(int stubPid)
            {
                Polls++;
                return Engines(Polls);
            }

            public int? WaitForExit(EngineProc proc)
            {
                Waited.Add(proc.Pid);
                return Exit(proc);
            }
        }

        static EngineProc Ours(int pid) => new EngineProc { Pid = pid, Inspectable = true, SamePath = true, StartedAfterStub = true };

        static void Waiting()
        {
            // Per-user: no hand-off, the stub's own code comes back after the grace.
            var p = new FakeProbe();
            Check(EngineWait.AfterStubExit(10, 0, p) == 0, "per-user success returns 0");
            Check(p.Waited.Count == 0 && p.Now - new DateTime(2026, 10, 6, 0, 0, 0, DateTimeKind.Utc) >= EngineWait.HandoffGrace, "looked for a hand-off for the whole grace");
            Check(EngineWait.AfterStubExit(10, 2, new FakeProbe()) == 2, "UAC declined / cancelled code passes through");

            // All-users: elevated engine shows up, host waits and returns ITS code.
            foreach (var code in new[] { 0, 3, 5, 7 })
            {
                var handoffs = 0;
                var q = new FakeProbe { Engines = n => n >= 3 ? new[] { Ours(99) } : Array.Empty<EngineProc>(), Exit = _ => code };
                Check(EngineWait.AfterStubExit(10, 0, q, () => handoffs++) == code, "elevated exit code " + code + " returned");
                Check(handoffs == 1 && q.Waited.SequenceEqual(new[] { 99 }), "hand-off signalled once and engine waited for");
            }

            // Exit code unreadable: fall back to the stub's code (0 after UAC Yes).
            var r = new FakeProbe { Engines = _ => new[] { Ours(99) }, Exit = _ => null };
            Check(EngineWait.AfterStubExit(10, 0, r) == 0, "unknown elevated code -> stub code");

            // Leftover engines from another run are not ours.
            var stale = new[]
            {
                new EngineProc { Pid = 50, Inspectable = true, SamePath = false, StartedAfterStub = true },
                new EngineProc { Pid = 51, Inspectable = true, SamePath = true, StartedAfterStub = false },
            };
            var s = new FakeProbe { Engines = _ => stale };
            Check(EngineWait.AfterStubExit(10, 1, s) == 1 && s.Waited.Count == 0, "stale engines ignored");

            // An engine we may not inspect (other admin account) is assumed ours.
            var hidden = new FakeProbe { Engines = _ => new[] { new EngineProc { Pid = 77, Inspectable = false } }, Exit = _ => null };
            Check(EngineWait.AfterStubExit(10, 0, hidden) == 0 && hidden.Waited.SequenceEqual(new[] { 77 }), "uninspectable engine waited for");
        }

        const string Sha = "3c1ceecc4a5b557994218d15e35e249673e08372c1be7975c4255be4edcd5f74";

        // collect/app-version as the Store sends it (1.2.417).
        static Dictionary<string, object?> Feed(Action<Dictionary<string, object?>>? edit = null)
        {
            var payload = new Dictionary<string, object?>
            {
                ["currentVersion"] = "1.2.417",
                ["installerUrl"] = "/api/v1/files/01a16029-491b-7231-a3b1-7e96195d04da/content",
                ["installerSha256"] = Sha.ToUpperInvariant(),
                ["size"] = 168584000,
                ["downloadsPaused"] = false,
                ["pauseReason"] = "",
            };
            edit?.Invoke(payload);
            return new Dictionary<string, object?> { ["ok"] = true, ["payload"] = payload };
        }

        static bool Refused(Action act)
        {
            try { act(); return false; }
            catch (InvalidOperationException) { return true; }
        }

        static void WebFeed()
        {
            var r = WebRelease.Parse(Feed());
            Check(r.Version == "1.2.417" && r.Size == 168584000 && r.Sha256 == Sha && !r.Paused, "feed parsed, sha lower-cased");
            Check(WebRelease.Absolute(WebRelease.DefaultBaseUrl, r.InstallerUrl) == "https://uefnducky.org/api/v1/files/01a16029-491b-7231-a3b1-7e96195d04da/content", "relative installer URL on the Store");
            Check(Refused(() => WebRelease.Parse(Feed(p => p["installerSha256"] = "abc"))), "short sha refused");
            Check(Refused(() => WebRelease.Parse(Feed(p => p["installerSha256"] = new string('z', 64)))), "non-hex sha refused");
            Check(Refused(() => WebRelease.Parse(Feed(p => p["size"] = 0))), "no size refused");
            Check(Refused(() => WebRelease.Parse(Feed(p => p.Remove("installerUrl")))), "no installer refused");
            Check(Refused(() => WebRelease.Parse(new Dictionary<string, object?> { ["ok"] = false, ["error"] = "forbidden" })), "Store error refused");
            var paused = WebRelease.Parse(Feed(p => { p["downloadsPaused"] = true; p["pauseReason"] = "Fixing a bug"; p.Remove("installerUrl"); }));
            Check(paused.Paused && paused.PauseReason == "Fixing a bug", "paused downloads reported, not refused");

            Check(WebRelease.BaseUrl(null) == "https://uefnducky.org" && WebRelease.BaseUrl("  ") == "https://uefnducky.org", "default Store");
            Check(WebRelease.BaseUrl("http://127.0.0.1:8765/") == "http://127.0.0.1:8765", "loopback rehearsal feed allowed");
            Check(WebRelease.BaseUrl("http://evil.example") == "https://uefnducky.org", "plain http elsewhere ignored");
            Check(WebRelease.FeedUrl("https://uefnducky.org") == "https://uefnducky.org/api/v1/plugins/uefn-ducky-store/collect/app-version", "feed URL");
            Check(Refused(() => WebRelease.Absolute(WebRelease.DefaultBaseUrl, "http://evil.example/setup.exe")), "absolute http installer refused");
            Check(WebRelease.Absolute(WebRelease.DefaultBaseUrl, "https://cdn.example/a.exe") == "https://cdn.example/a.exe", "absolute https installer kept");
        }

        static void WebDownload()
        {
            Check(WebRelease.FileName("1.2.417") == "UEFN-Ducky-Setup-1.2.417.exe", "download name");
            Check(WebRelease.FileName(@"..\..\x 1.2") == "UEFN-Ducky-Setup-....x1.2.exe", "no path characters in the name");
            Check(WebRelease.FileName("") == "UEFN-Ducky-Setup-latest.exe", "empty version");

            Check(WebRelease.ResumeFrom(0, 100) == 0 && WebRelease.ResumeFrom(40, 100) == 40 && WebRelease.ResumeFrom(100, 100) == 100, "resume from the part's length");
            Check(WebRelease.ResumeFrom(101, 100) == 0, "a part longer than the file starts over");
            Check(WebRelease.AltUrl("https://uefnducky.org/api/v1/files/app-release/latest") == "https://uefnducky.org/api/v1/files/app-release/latest?alt=1", "second storage host for the latest Setup");
            Check(WebRelease.AltUrl("https://uefnducky.org/api/v1/files/01a1/content?v=2") == "https://uefnducky.org/api/v1/files/01a1/content?v=2&alt=1", "second host keeps the link's own query");
            Check(WebRelease.AltUrl("https://uefnducky.org/api/v1/files/01a1/content?alt=1") == "", "a link that asks already has no other");
            Check(WebRelease.AltUrl("https://cdn.example/a.exe") == "" && WebRelease.AltUrl("not a url") == "", "only the Store's download routes have a second host");
            Check(WebRelease.WorthSecondHost(new WebException("cut", WebExceptionStatus.SecureChannelFailure)), "a cut TLS handshake tries the second host");
            Check(WebRelease.WorthSecondHost(new WebException("cut", WebExceptionStatus.ConnectionClosed)), "a closed connection tries the second host");
            Check(WebRelease.WorthSecondHost(new System.IO.IOException("cut", new System.Net.Sockets.SocketException(10054))), "a reset mid-download tries the second host");
            Check(!WebRelease.WorthSecondHost(new WebException("404", WebExceptionStatus.ProtocolError)), "an answer from the server does not");
            Check(!WebRelease.WorthSecondHost(new System.IO.IOException("The download is bigger than the Store said.")) && !WebRelease.WorthSecondHost(null), "a disk or size problem does not");

            Check(WebRelease.DownloadPercent(0, 100) == 0 && WebRelease.DownloadPercent(50, 100) == 35 && WebRelease.DownloadPercent(100, 100) == WebRelease.DownloadShare, "download fills the first part of the bar");
            Check(WebRelease.DownloadPercent(5, 0) == 0 && WebRelease.DownloadPercent(200, 100) == WebRelease.DownloadShare, "download percent clamped");
            Check(WebRelease.InstallPercent(0) == WebRelease.DownloadShare && WebRelease.InstallPercent(100) == 100 && WebRelease.InstallPercent(50) == 85, "install fills the rest");
            Check(WebRelease.DownloadStatus(47185920, 168584000) == "Downloading UEFN Ducky · 45 of 161 MB", "download status in MB");

            Check(WebRelease.SignerOk("CN=\"Mindful Path Company, LLC\", O=\"Mindful Path Company, LLC\", L=Hull, S=Georgia, C=US"), "our signer accepted");
            Check(!WebRelease.SignerOk("CN=\"Mindful Path Company, LLC\", O=\"Someone Else\", C=US"), "our name only in CN refused");
            Check(!WebRelease.SignerOk("CN=Evil, O=\"Mindful Path Company, LLC Ltd\""), "look-alike organization refused");
            Check(!WebRelease.SignerOk(""), "unsigned refused");

            Check(WebRelease.SameSha(Sha, Sha.ToUpperInvariant()) && !WebRelease.SameSha("", "") && !WebRelease.SameSha(Sha, Sha.Substring(1) + "0"), "sha compare");

            DownloadRetries();

            var files = new[] { @"C:\d\UEFN-Ducky-Setup-1.2.416.exe", @"C:\d\UEFN-Ducky-Setup-1.2.417.exe", @"C:\d\UEFN-Ducky-Setup-1.2.417.exe.part", @"C:\d\UEFN-Ducky-Setup-1.2.415.exe.part" };
            Check(WebRelease.Stale(files, "UEFN-Ducky-Setup-1.2.417.exe").SequenceEqual(new[] { files[0], files[3] }), "other versions' downloads swept, ours kept");
        }

        static void DownloadRetries()
        {
            Check(WebRelease.DownloadRetryWaits.SequenceEqual(new[] { 2, 5, 10, 20, 30 }), "download retried after 2, 5, 10, 20, 30 s");

            // Fails twice (each try adds bytes), then finishes: no error, waits start over each time.
            long have = 0;
            var tries = 0;
            var waits = new List<int>();
            var last = WebRelease.FetchWithRetries(() =>
            {
                tries++;
                have += 40;
                if (tries <= 2) throw new WebException("drop", WebExceptionStatus.ReceiveFailure);
            }, () => have, waits.Add);
            Check(last == null && tries == 3 && waits.SequenceEqual(new[] { 2, 2 }), "fails twice then finishes still installs");

            // No progress at all: 6 tries, every wait used, last error returned.
            tries = 0;
            waits.Clear();
            var dead = new WebException("dns", WebExceptionStatus.NameResolutionFailure);
            last = WebRelease.FetchWithRetries(() => { tries++; throw dead; }, () => 0, waits.Add);
            Check(last == dead && tries == 6 && waits.SequenceEqual(WebRelease.DownloadRetryWaits), "dead network gives up after 6 tries");

            // A 25 s drop (no bytes on tries 1-3) resumes on try 4.
            tries = 0;
            waits.Clear();
            last = WebRelease.FetchWithRetries(() => { if (++tries < 4) throw new System.IO.IOException("reset"); }, () => 0, waits.Add);
            Check(last == null && tries == 4 && waits.Sum() == 17, "a 25 s drop resumes on try 4");

            Check(WebRelease.DownloadErrorCode(new WebException("t", WebExceptionStatus.Timeout)) == "download_failed.timeout", "download timeout");
            Check(WebRelease.DownloadErrorCode(dead) == "download_failed.dns", "download dns");
            Check(new[] { WebExceptionStatus.ConnectFailure, WebExceptionStatus.ConnectionClosed, WebExceptionStatus.ReceiveFailure }
                .All(s => WebRelease.DownloadErrorCode(new WebException("c", s)) == "download_failed.conn"), "download conn");
            Check(WebRelease.DownloadErrorCode(new WebException("h", WebExceptionStatus.ProtocolError), 503) == "download_failed.http_503", "download http status");
            Check(WebRelease.DownloadErrorCode(new System.IO.IOException("disk")) == "download_failed.io", "download io");
            Check(WebRelease.DownloadErrorCode(null) == "download_failed.short", "download short");
            Check(WebRelease.DownloadErrorCode(new WebException("u", WebExceptionStatus.UnknownError)) == "download_failed.other", "download other");
            Check(WebRelease.DownloadErrorCode(new WebException("h", WebExceptionStatus.ProtocolError), 503).Length <= 48, "code fits usage_events");
        }

        static void StoreErrors()
        {
            Check(WebRelease.StoreRetryWaits.SequenceEqual(new[] { 1, 3, 9 }), "Store request retried after 1, 3 and 9 s");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.NameResolutionFailure, 0) == "store_unreachable.dns", "dns");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.Timeout, 0) == "store_unreachable.timeout", "timeout");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.TrustFailure, 0) == "store_unreachable.tls"
                && WebRelease.StoreErrorCode(WebExceptionStatus.SecureChannelFailure, 0) == "store_unreachable.tls", "tls");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.ProxyNameResolutionFailure, 0) == "store_unreachable.proxy"
                && WebRelease.StoreErrorCode(WebExceptionStatus.RequestProhibitedByProxy, 0) == "store_unreachable.proxy", "proxy");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.ProtocolError, 503) == "store_unreachable.http_503", "http status");
            Check(WebRelease.StoreErrorCode(WebExceptionStatus.ProtocolError, 0) == "store_unreachable.other"
                && WebRelease.StoreErrorCode(WebExceptionStatus.ConnectFailure, 0) == "store_unreachable.other"
                && WebRelease.StoreErrorCode(WebExceptionStatus.UnknownError, 0) == "store_unreachable.other", "other");

            // Try again: a failed or cancelled Store request is asked again, a good one reused.
            Check(WebRelease.NeedsFetch(null), "first try fetches");
            Check(WebRelease.NeedsFetch(Task.FromException(new WebException("down"))), "Try again after a failure fetches again");
            Check(WebRelease.NeedsFetch(Task.FromCanceled(new System.Threading.CancellationToken(true))), "Try again after a cancel fetches again");
            Check(!WebRelease.NeedsFetch(Task.FromResult(new StoreRelease())), "a good answer is reused");
        }

        // Stats.cs: what Setup and the uninstaller report; the Store checks the same names.
        static void Counts()
        {
            Check(Stats.Kind(false, false) == "new" && Stats.Kind(false, true) == "reinstall" && Stats.Kind(true, true) == "update" && Stats.Kind(true, false) == "update", "run kinds");
            Check(Stats.IsInstallId("0123456789abcdef0123456789abcdef"), "lowercase hex id");
            Check(!Stats.IsInstallId("0123456789ABCDEF0123456789abcdef") && !Stats.IsInstallId("abc") && !Stats.IsInstallId(""), "bad ids refused");
            var id = Stats.NewInstallId(Enumerable.Range(0, 16).Select(i => (byte)(i * 17)).ToArray());
            Check(id == "00112233445566778899aabbccddeeff" && Stats.IsInstallId(id), "new id is 32 hex");

            Check(Stats.AgeBucket(-1) == "unknown" && Stats.AgeBucket(3600) == "day0" && Stats.AgeBucket(3 * 86400) == "week1"
                && Stats.AgeBucket(20 * 86400) == "month1" && Stats.AgeBucket(90 * 86400) == "older", "install age buckets");
            Check(Stats.TookBucket(30) == "1m" && Stats.TookBucket(200) == "5m" && Stats.TookBucket(900) == "20m" && Stats.TookBucket(5000) == "more", "install time buckets");
            Check(Stats.ExitCode(0) == "ok" && Stats.ExitCode(5) == "exit_5" && Stats.ExitCode(-2) == "exit_neg", "exit codes");
            // Nov 10 2026: a failed web update sent exit_neg and nothing said whether Setup was ended or crashed.
            Check(Stats.ExitCode(unchecked((int)0xC000013A)) == "exit_killed" && Stats.ExitCode(-1073741510) == "exit_killed"
                && Stats.ExitCode(unchecked((int)0xC0000005)) == "exit_crashed" && Stats.ExitCode(unchecked((int)0xC0000409)) == "exit_crashed"
                && Stats.FailCode(unchecked((int)0xC0000005), "access_denied") == "exit_crashed.access_denied", "exit codes: ended from outside vs crashed");
            Check(Stats.Outcome(true, true) == "ok" && Stats.Outcome(false, true) == "cancel" && Stats.Outcome(false, false) == "fail", "outcomes");
            Check(Stats.FailReason("2026-10-28 DeleteFile failed; code 32.\r\nThe process cannot access the file because it is being used by another process.") == "file_in_use", "locked file");
            Check(Stats.FailReason("CopyFile failed; code 5.\nAccess is denied.") == "access_denied", "no permission");
            Check(Stats.FailReason("There is not enough space on the disk.") == "disk_full" && Stats.FailReason("") == "" && Stats.FailReason(null) == "", "full disk and nothing said");
            Check(Stats.FailCode(4, "file_in_use") == "exit_4.file_in_use" && Stats.FailCode(4, "") == "exit_4" && Stats.FailCode(0, "file_in_use") == "ok", "fail codes");
            Check(Stats.HostFailReason("System.ComponentModel.Win32Exception (225): Operation did not complete successfully because the file contains a virus or potentially unwanted software.") == "antivirus", "exit_1: antivirus");
            Check(Stats.HostFailReason("System.InvalidOperationException: The Store's download is a web installer, not the full Setup: stopping here.") == "web_child", "exit_1: web child");
            // A PC with Microsoft Edge removed: Setup's window cannot start, and it says what to install.
            Check(Stats.IsWebView2Missing("WebView2RuntimeNotFoundException: Couldn't find a compatible Webview2 Runtime installation to host WebViews.")
                && Stats.HostFailReason("Microsoft.Web.WebView2.Core.WebView2RuntimeNotFoundException: Couldn't find a compatible Webview2 Runtime installation to host WebViews.") == "webview2_missing"
                && !Stats.IsWebView2Missing("System.IO.FileNotFoundException: Microsoft.Web.WebView2.Core.dll")
                && Stats.WebView2MissingText.Contains(Stats.WebView2Page), "exit_1: WebView2 runtime missing");
            Check(Stats.HostFailReason("System.UnauthorizedAccessException: Access to the path 'C:\\x\\Setup-engine.exe' is denied.") == "access_denied", "exit_1: no permission");
            Check(Stats.HostFailReason("System.IO.IOException: There is not enough space on the disk.") == "disk_full", "exit_1: full disk");
            Check(Stats.HostFailReason("System.InvalidOperationException: Could not start Setup-engine.exe") == "engine_start", "exit_1: engine start");
            Check(Stats.HostFailReason("System.NullReferenceException: boom") == "host_error" && Stats.HostFailReason("") == "", "exit_1: other host error");
            Check(Stats.ExitOneReason("", "") == "no_log" && Stats.ExitOneReason("Log opened.", "") == "" && Stats.ExitOneReason("", "Could not start x") == "engine_start", "exit_1: logs");
            Check(Stats.FailCode(1, Stats.ExitOneReason("", "")) == "exit_1.no_log", "exit_1 code");

            // exit_4 always says why: Inno's own lines, then the log's last line as a last resort.
            Check(Stats.FailReason("2026-11-06 10:12:33.120   Setup was not completed.") == "inno_not_completed", "exit_4: setup not completed");
            Check(Stats.FailCode(4, Stats.FailReason("Setup was not completed.")) == "exit_4.inno_not_completed", "exit_4.inno_not_completed code");
            Check(Stats.FailReason("RenameFile failed; error 5") == "access_denied" && Stats.FailReason("error 50 something") == "", "exit_4: error 5");
            Check(Stats.FailReason("DeleteFile failed; error 32") == "file_in_use" && Stats.FailReason("error 321") == "", "exit_4: error 32");
            Check(Stats.FailReason("Access is denied. Setup was not completed.") == "access_denied", "a known cause beats not completed");
            var unknown = Stats.UnknownReason("Log opened.\r\n2026-11-06 10:12:33.120   Exception: Something odd broke here, very long text indeed\r\n\r\n");
            Check(unknown == "unknown_exception_something_odd_broke_he" && Stats.FailCode(4, unknown).Length <= 48, "exit_4: unknown_ last line, fits usage_events");
            Check(Stats.UnknownReason("") == "unknown" && Stats.UnknownReason(null) == "unknown" && Stats.UnknownReason("12:00:01.5 ") == "unknown", "exit_4: empty log");

            // Downloads paused: a feed that clears after 2 checks goes on with the install.
            var answers = new Queue<StoreRelease>(new[] { new StoreRelease { Paused = true }, new StoreRelease { Version = "1.2.488" } });
            var pauseWaits = new List<int>();
            var checks = new List<int>();
            var cleared = Stats.WaitWhilePaused(new StoreRelease { Paused = true }, r => r.Paused, answers.Dequeue, pauseWaits.Add, checks.Add);
            Check(!cleared.Paused && cleared.Version == "1.2.488" && pauseWaits.SequenceEqual(new[] { 60, 60 }) && checks.SequenceEqual(new[] { 1, 2 }), "paused feed clears after 2 checks");
            var tries = 0;
            var still = Stats.WaitWhilePaused(new StoreRelease { Paused = true }, r => r.Paused,
                () => { if (++tries % 2 == 0) throw new WebException("down"); return new StoreRelease { Paused = true }; }, _ => { });
            Check(still.Paused && tries == Stats.PauseChecks, "still paused after 10 checks (a failed recheck keeps waiting)");
            Check(!Stats.WaitWhilePaused(new StoreRelease(), r => r.Paused, () => throw new Exception("never"), _ => throw new Exception("never")).Paused, "not paused: no wait");
            var stopped = false;
            try { Stats.WaitWhilePaused(new StoreRelease { Paused = true }, r => r.Paused, () => new StoreRelease { Paused = true }, _ => throw new OperationCanceledException()); }
            catch (OperationCanceledException) { stopped = true; }
            Check(stopped, "cancel stops the wait");
            Check(Stats.PausedStatus(3).Contains("Waiting for the UEFN Ducky Store") && Stats.PausedStatus(3).Contains("3 of 10"), "waiting screen text");
            Check(Stats.Reason("bugs") == "bugs" && Stats.Reason("  slow ") == "slow" && Stats.Reason("hacked") == "none" && Stats.Reason(null) == "none", "reasons");
            Check(Stats.Screen("done") == "done" && Stats.Screen("<b>") == "welcome", "screens");
            Check(Stats.IsRetry("error") && !Stats.IsRetry("welcome") && !Stats.IsRetry("options") && !Stats.IsRetry(null), "install from the error screen counts as a retry");
            Check(Stats.Os(22631) == "win11" && Stats.Os(19045) == "win10", "Windows version");
            Check(Stats.Browser("MSEdgeHTM") == "edge" && Stats.Browser("ChromeHTML") == "chrome" && Stats.Browser("BraveHTML") == "chrome"
                && Stats.Browser("FirefoxURL-308046B0AF4A39CB") == "firefox" && Stats.Browser("Opera GXStable") == "opera"
                && Stats.Browser("VivaldiHTM.ABC") == "other" && Stats.Browser(null) == "other", "default browser from its ProgId");

            // "Open UEFN Ducky": one look at the app's boot record, then what to send.
            Check(Stats.LaunchLook(false, "ok", true) == "", "an older record says nothing about this start");
            Check(Stats.LaunchLook(true, "ok", true) == "started" && Stats.LaunchLook(true, "fatal", false) == "error"
                && Stats.LaunchLook(true, "stuck", true) == "stuck" && Stats.LaunchLook(true, "closed", false) == "closed"
                && Stats.LaunchLook(true, "stopped", false) == "closed", "final boot states");
            Check(Stats.LaunchLook(true, "starting", true) == "" && Stats.LaunchLook(true, "starting", false) == "died"
                && Stats.LaunchLook(true, "handoff", false) == "" && Stats.LaunchLook(true, null, false) == "died", "still starting, died, or handed off");
            Check(Stats.LaunchVerdict("started", 0, true, false) == "started", "started is sent at once");
            Check(Stats.LaunchVerdict("died", 3, true, false) == "" && Stats.LaunchVerdict("died", Stats.LaunchSettleSeconds, true, false) == "died"
                && Stats.LaunchVerdict("died", 3, true, true) == "died", "a failure counts once it held, or when time is up");
            Check(Stats.LaunchVerdict("", 40, true, false) == "" && Stats.LaunchVerdict("", 90, true, true) == "slow"
                && Stats.LaunchVerdict("", 90, false, true) == "never_appeared", "time up: still starting, or no record at all");
            Check(Stats.LaunchTook(3) == "5s" && Stats.LaunchTook(12) == "15s" && Stats.LaunchTook(40) == "60s" && Stats.LaunchTook(80) == "more", "start time buckets");
            Check(Stats.Stage("http_server") == "http_server" && Stats.Stage("hello") == "other" && Stats.Stage(@"C:\Users\x") == "other" && Stats.Stage(null) == "other", "boot stage is a listed word");

            Check(Stats.ExePath("\"C:\\Program Files\\UEFN Ducky\\unins000.exe\" /SILENT") ==@"C:\Program Files\UEFN Ducky\unins000.exe", "quoted uninstall command");
            Check(Stats.ExePath(@"C:\Apps\unins000.exe /SILENT") == @"C:\Apps\unins000.exe" && Stats.ExePath("") == "" && Stats.ExePath("\"") == "", "plain and empty commands");

            Check(Stats.CleanText("  hi\r\nthere\u0007 ") == "hi\nthere", "control characters dropped, line breaks kept");
            Check(Stats.CleanText(new string('x', 900)).Length == Stats.TextMax && Stats.CleanText(null) == "", "text capped");
        }
    }
}
