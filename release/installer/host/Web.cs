using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace DuckySetup
{
    // A web install step that failed, with the code Setup's anonymous counts report.
    internal sealed class SetupFailure : InvalidOperationException
    {
        public string Code { get; }

        public SetupFailure(string code, string message, Exception? inner = null) : base(message, inner)
        {
            Code = code;
        }
    }

    // The web installer: a Setup built without Setup-engine.exe inside. It asks the
    // Store for the latest release, downloads that release's full Setup, checks its
    // size, SHA-256 and Authenticode signer, then runs it silently with the same
    // switches the in-app updater uses, and shows the engine's progress in its own
    // window. Nothing from the download runs before every check passed.
    internal static class Web
    {
        public const string CheckSwitch = "/CHECKDOWNLOAD";
        // Set on the downloaded Setup it starts. A web installer that finds it was started
        // by one (the Store pointing at the web installer, not the full one) stops instead
        // of downloading and starting itself again.
        const string ChildEnv = "DUCKY_WEB_SETUP_CHILD";

        public static bool IsWeb => Embed.Asm.GetManifestResourceInfo("Setup-engine.exe") is null;

        public static string DownloadDir => Path.Combine(Engine.AppDataRoot, "setup-download");

        static string BaseUrl => WebRelease.BaseUrl(Environment.GetEnvironmentVariable(WebRelease.BaseUrlEnv));

        public static string UserAgent()
        {
            var v = typeof(Web).Assembly.GetName().Version;
            return "UEFN-Ducky-Setup/" + (v == null ? "0" : v.ToString(3));
        }

        public static StoreRelease FetchLatest()
        {
            var baseUrl = BaseUrl;
            string json;
            for (var attempt = 0; ; attempt++)
            {
                try
                {
                    json = PostFeed(baseUrl);
                    break;
                }
                catch (Exception ex) when (ex is WebException || ex is IOException)
                {
                    if (attempt < WebRelease.StoreRetryWaits.Length)
                    {
                        Thread.Sleep(TimeSpan.FromSeconds(WebRelease.StoreRetryWaits[attempt]));
                        continue;
                    }
                    var w = ex as WebException;
                    var http = w?.Response is HttpWebResponse r ? (int)r.StatusCode : 0;
                    throw new SetupFailure(
                        WebRelease.StoreErrorCode(w?.Status ?? WebExceptionStatus.UnknownError, http),
                        "Can't reach the UEFN Ducky Store. Check your internet connection and try again. (" + ex.Message + ")", ex);
                }
            }
            try
            {
                if (!(new JavaScriptSerializer().DeserializeObject(json) is Dictionary<string, object> envelope))
                    throw new InvalidOperationException("The UEFN Ducky Store sent an answer Setup can't read.");
                var release = WebRelease.Parse(envelope!);
                if (!release.Paused)
                    release.InstallerUrl = WebRelease.Absolute(baseUrl, release.InstallerUrl);
                return release;
            }
            catch (InvalidOperationException ex) when (!(ex is SetupFailure))
            {
                throw new SetupFailure("store_answer", ex.Message, ex);
            }
        }

        static string PostFeed(string baseUrl)
        {
            var req = (HttpWebRequest)WebRequest.Create(WebRelease.FeedUrl(baseUrl));
            req.Method = "POST";
            req.Accept = "application/json";
            req.ContentType = "application/json";
            req.UserAgent = UserAgent();
            // Collect endpoints want Origin to match the site, like the app's own check.
            req.Headers["Origin"] = baseUrl;
            req.Timeout = 20000;
            req.ReadWriteTimeout = 20000;
            var body = Encoding.UTF8.GetBytes("{}");
            req.ContentLength = body.Length;
            using (var s = req.GetRequestStream())
                s.Write(body, 0, body.Length);
            using var resp = (HttpWebResponse)req.GetResponse();
            using var reader = new StreamReader(resp.GetResponseStream()!, Encoding.UTF8);
            return reader.ReadToEnd();
        }

        // Downloads (or reuses) the release's full Setup and returns its path once it
        // passed every check. progress(got, total) runs on this thread.
        public static string Download(StoreRelease release, Action<long, long>? progress, CancellationToken ct)
        {
            if (release.Paused)
                throw new SetupFailure(
                    "paused",
                    "Downloads are paused right now" + (release.PauseReason.Length > 0 ? ": " + release.PauseReason : ".") + " Try again later.");
            Directory.CreateDirectory(DownloadDir);
            var name = WebRelease.FileName(release.Version);
            var final = Path.Combine(DownloadDir, name);
            var part = final + ".part";
            foreach (var old in WebRelease.Stale(Directory.GetFiles(DownloadDir), name))
            {
                try { File.Delete(old); }
                catch { /* still in use: the next run sweeps it */ }
            }
            // A finished download from an earlier try is reused when it still checks out.
            if (File.Exists(final))
            {
                try
                {
                    Verify(final, release);
                    progress?.Invoke(release.Size, release.Size);
                    return final;
                }
                catch (InvalidOperationException)
                {
                    TryDelete(final);
                }
            }
            Exception? last = null;
            for (var attempt = 0; attempt < 4; attempt++)
            {
                ct.ThrowIfCancellationRequested();
                if (attempt > 0)
                    ct.WaitHandle.WaitOne(TimeSpan.FromSeconds(2 * attempt));
                ct.ThrowIfCancellationRequested();
                try
                {
                    Fetch(release, part, progress, ct);
                    break;
                }
                catch (Exception ex) when (ex is WebException || ex is IOException)
                {
                    last = ex;
                }
            }
            if (!File.Exists(part) || new FileInfo(part).Length != release.Size)
                throw new SetupFailure(
                    "download_failed",
                    "The download didn't finish. Check your internet connection and try again."
                    + (last != null ? " (" + last.Message + ")" : ""), last);
            TryDelete(final);
            File.Move(part, final);
            try
            {
                Verify(final, release);
            }
            catch
            {
                TryDelete(final);
                throw;
            }
            return final;
        }

        static void Fetch(StoreRelease release, string part, Action<long, long>? progress, CancellationToken ct)
        {
            var have = File.Exists(part) ? new FileInfo(part).Length : 0;
            var from = WebRelease.ResumeFrom(have, release.Size);
            if (from == release.Size)
                return;
            var req = (HttpWebRequest)WebRequest.Create(release.InstallerUrl);
            req.UserAgent = UserAgent();
            req.AllowAutoRedirect = true;
            req.Timeout = 30000;
            req.ReadWriteTimeout = 60000;
            if (from > 0)
                req.AddRange(from);
            using var cancel = ct.Register(() => { try { req.Abort(); } catch { /* already done */ } });
            using var resp = (HttpWebResponse)req.GetResponse();
            // A server that ignores Range sends the whole file again: start over.
            if (from > 0 && resp.StatusCode != HttpStatusCode.PartialContent)
                from = 0;
            using var src = resp.GetResponseStream()!;
            using var dst = new FileStream(part, from > 0 ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.Read);
            var buf = new byte[1 << 20];
            var got = from;
            var lastReport = Environment.TickCount;
            progress?.Invoke(got, release.Size);
            while (true)
            {
                ct.ThrowIfCancellationRequested();
                var n = src.Read(buf, 0, buf.Length);
                if (n <= 0)
                    break;
                if (got + n > release.Size)
                    throw new IOException("The download is bigger than the Store said.");
                dst.Write(buf, 0, n);
                got += n;
                if (unchecked(Environment.TickCount - lastReport) >= 200)
                {
                    lastReport = Environment.TickCount;
                    progress?.Invoke(got, release.Size);
                }
            }
            progress?.Invoke(got, release.Size);
        }

        // Size, SHA-256 from the Store, then a valid Authenticode signature by us.
        public static void Verify(string path, StoreRelease release)
        {
            var info = new FileInfo(path);
            if (!info.Exists || info.Length != release.Size)
                throw new SetupFailure("verify_failed", "The download isn't the size the Store said. Try again.");
            if (!WebRelease.SameSha(Sha256(path), release.Sha256))
                throw new SetupFailure("verify_failed", "The download doesn't match the Store's checksum. Try again.");
            if (!SignatureValid(path))
                throw new SetupFailure("verify_failed", "The download isn't signed correctly, so Setup won't run it.");
            string subject;
            try { subject = new X509Certificate2(X509Certificate.CreateFromSignedFile(path)).Subject; }
            catch (CryptographicException) { subject = ""; }
            if (!WebRelease.SignerOk(subject))
                throw new SetupFailure("verify_failed", "The download isn't signed by UEFN Ducky's publisher, so Setup won't run it.");
        }

        static string Sha256(string path)
        {
            using var sha = SHA256.Create();
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            var hash = sha.ComputeHash(fs);
            var sb = new StringBuilder(hash.Length * 2);
            foreach (var b in hash)
                sb.Append(b.ToString("x2"));
            return sb.ToString();
        }

        static bool SignatureValid(string path)
        {
            var file = new Native.WinTrustFileInfo
            {
                cbStruct = (uint)Marshal.SizeOf(typeof(Native.WinTrustFileInfo)),
                pcwszFilePath = path,
            };
            var filePtr = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Native.WinTrustFileInfo)));
            try
            {
                Marshal.StructureToPtr(file, filePtr, false);
                var data = new Native.WinTrustData
                {
                    cbStruct = (uint)Marshal.SizeOf(typeof(Native.WinTrustData)),
                    dwUIChoice = Native.WtdUiNone,
                    fdwRevocationChecks = Native.WtdRevokeNone,
                    dwUnionChoice = Native.WtdChoiceFile,
                    pFile = filePtr,
                    dwStateAction = Native.WtdStateActionVerify,
                    dwProvFlags = Native.WtdCacheOnlyUrlRetrieval,
                };
                var result = Native.WinVerifyTrust(IntPtr.Zero, Native.GenericVerifyV2, ref data);
                data.dwStateAction = Native.WtdStateActionClose;
                Native.WinVerifyTrust(IntPtr.Zero, Native.GenericVerifyV2, ref data);
                return result == 0;
            }
            finally
            {
                Marshal.DestroyStructure(filePtr, typeof(Native.WinTrustFileInfo));
                Marshal.FreeHGlobal(filePtr);
            }
        }

        // Runs the verified full Setup the way the in-app updater does: silently, with
        // the host's own switches. It extracts and runs its engine, follows the UAC
        // hand-off for /ALLUSERS and exits with the engine's exit code.
        public static Process StartSetup(string setupPath, string[] args)
        {
            var silent = new List<string>(args);
            if (!HostArgs.IsSilent(silent))
                silent.Insert(0, "/VERYSILENT");
            var psi = new ProcessStartInfo
            {
                FileName = setupPath,
                Arguments = HostArgs.Join(silent),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.EnvironmentVariables[ChildEnv] = "1";
            return Process.Start(psi) ?? throw new InvalidOperationException("Could not start the downloaded Setup.");
        }

        // Done with the download (installed): drop it so it doesn't sit in AppData.
        public static void Cleanup(string? path)
        {
            if (!string.IsNullOrEmpty(path))
                TryDelete(path!);
        }

        // /VERYSILENT on the web installer (scripted installs): download, check, run.
        public static int RunSilent(string[] args)
        {
            if (Environment.GetEnvironmentVariable(ChildEnv) == "1")
                throw new InvalidOperationException("The Store's download is a web installer, not the full Setup: stopping here.");
            var release = FetchLatest();
            var path = Download(release, null, CancellationToken.None);
            using var proc = StartSetup(path, args);
            proc.WaitForExit();
            var code = proc.ExitCode;
            if (code == 0)
                Cleanup(path);
            return code;
        }

        // /CHECKDOWNLOAD: download and check the latest full Setup, print the result
        // and delete it. Never runs anything it downloaded (release check).
        public static int CheckDownload()
        {
            try
            {
                var release = FetchLatest();
                var path = Download(release, null, CancellationToken.None);
                var size = new FileInfo(path).Length;
                Cleanup(path);
                Console.WriteLine("ok " + release.Version + " " + size + " " + release.Sha256);
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine("failed: " + ex.Message);
                Engine.LogHostError(ex);
                return 1;
            }
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); }
            catch { /* in use; swept on the next run */ }
        }
    }
}
