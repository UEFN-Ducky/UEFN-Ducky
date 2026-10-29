using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Threading.Tasks;

namespace DuckySetup
{
    // The web installer's pure rules: the Store feed, URLs, resume, progress and
    // signer checks. No WinForms / WebView2 / Win32 / network, so host.tests can
    // compile it on its own.
    internal sealed class StoreRelease
    {
        public string Version = "";
        public string InstallerUrl = "";
        public string Sha256 = "";
        public long Size;
        public bool Paused;
        public string PauseReason = "";
    }

    internal static class WebRelease
    {
        public const string DefaultBaseUrl = "https://uefnducky.org";
        // Same override as the app's updater (frontend/version_check.py), for a local
        // rehearsal feed (build/upgrade_proof/serve_update_feed.py).
        public const string BaseUrlEnv = "DUCKY_UPDATE_BASE_URL";
        public const string FeedPath = "/api/v1/plugins/uefn-ducky-store/collect/app-version";
        // Only a full installer signed by this organization is ever run.
        public const string SignerOrganization = "Mindful Path Company, LLC";
        // The download is most of the wait; the install of the downloaded copy is the rest.
        public const int DownloadShare = 70;

        public static string BaseUrl(string? overrideUrl)
        {
            var text = (overrideUrl ?? "").Trim().TrimEnd('/');
            if (text.Length == 0)
                return DefaultBaseUrl;
            return Uri.TryCreate(text, UriKind.Absolute, out var uri) && Allowed(uri) ? text : DefaultBaseUrl;
        }

        public static string FeedUrl(string baseUrl) => baseUrl.TrimEnd('/') + FeedPath;

        // https anywhere; plain http only on this PC (a rehearsal feed).
        public static bool Allowed(Uri uri)
        {
            if (uri.Scheme == Uri.UriSchemeHttps)
                return true;
            return uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback;
        }

        // The feed's installer URL is relative to the Store ("/api/v1/files/<id>/content").
        public static string Absolute(string baseUrl, string url)
        {
            if (Uri.TryCreate(url, UriKind.Absolute, out var abs))
            {
                if (!Allowed(abs))
                    throw new InvalidOperationException("The Store gave an installer link that isn't https.");
                return abs.ToString();
            }
            return baseUrl.TrimEnd('/') + "/" + url.TrimStart('/');
        }

        // collect/app-version answers {"ok": true, "payload": {...}}.
        public static StoreRelease Parse(IDictionary<string, object?> envelope)
        {
            if (envelope.TryGetValue("ok", out var ok) && ok is bool b && !b)
                throw new InvalidOperationException("The UEFN Ducky Store didn't answer: " + Text(envelope, "error"));
            var payload = envelope.TryGetValue("payload", out var p) && p is IDictionary<string, object?> inner ? inner : envelope;
            var release = new StoreRelease
            {
                Version = Text(payload, "currentVersion"),
                InstallerUrl = Text(payload, "installerUrl"),
                Sha256 = Text(payload, "installerSha256").ToLowerInvariant(),
                Size = Number(payload, "size"),
                Paused = payload.TryGetValue("downloadsPaused", out var paused) && paused is bool pb && pb,
                PauseReason = Text(payload, "pauseReason"),
            };
            if (release.Paused)
                return release;
            if (release.Version.Length == 0 || release.InstallerUrl.Length == 0)
                throw new InvalidOperationException("The UEFN Ducky Store has no installer to download right now.");
            if (release.Sha256.Length != 64 || !IsHex(release.Sha256))
                throw new InvalidOperationException("The UEFN Ducky Store didn't say how to check the download.");
            if (release.Size <= 0)
                throw new InvalidOperationException("The UEFN Ducky Store didn't say how big the download is.");
            return release;
        }

        // "UEFN-Ducky-Setup-1.2.417.exe"; anything but digits, letters, dots and dashes is dropped.
        public static string FileName(string version)
        {
            var safe = new System.Text.StringBuilder();
            foreach (var c in version)
            {
                if (char.IsLetterOrDigit(c) || c == '.' || c == '-')
                    safe.Append(c);
            }
            return "UEFN-Ducky-Setup-" + (safe.Length > 0 ? safe.ToString() : "latest") + ".exe";
        }

        // Where a partial download carries on from: its length, unless it is already
        // longer than the file (a different file of the same name), then from scratch.
        public static long ResumeFrom(long partLength, long expectedSize)
        {
            if (partLength <= 0 || partLength > expectedSize)
                return 0;
            return partLength;
        }

        public static int DownloadPercent(long got, long total)
        {
            if (total <= 0)
                return 0;
            var fraction = Math.Max(0.0, Math.Min(1.0, (double)got / total));
            return (int)Math.Floor(fraction * DownloadShare);
        }

        public static int InstallPercent(int enginePercent)
        {
            var p = Math.Max(0, Math.Min(100, enginePercent));
            return DownloadShare + (int)Math.Round(p * (100 - DownloadShare) / 100.0);
        }

        public static string DownloadStatus(long got, long total)
        {
            return "Downloading UEFN Ducky · " + Megabytes(got) + " of " + Megabytes(total) + " MB";
        }

        static string Megabytes(long bytes) =>
            ((long)Math.Round(bytes / (1024.0 * 1024.0))).ToString(CultureInfo.InvariantCulture);

        // X509Certificate.Subject, e.g. CN="Mindful Path Company, LLC", O="Mindful Path Company, LLC", L=Hull, ...
        public static bool SignerOk(string subject)
        {
            if (string.IsNullOrEmpty(subject))
                return false;
            return subject.IndexOf("O=\"" + SignerOrganization + "\"", StringComparison.Ordinal) >= 0
                || subject.IndexOf("O=" + SignerOrganization + ",", StringComparison.Ordinal) >= 0
                || subject.EndsWith("O=" + SignerOrganization, StringComparison.Ordinal);
        }

        // Seconds between tries of the Store's version request: 4 tries in all.
        public static readonly int[] StoreRetryWaits = { 1, 3, 9 };

        // setup.result code when the Store stayed unreachable, named after why.
        public static string StoreErrorCode(WebExceptionStatus status, int httpStatus)
        {
            switch (status)
            {
                case WebExceptionStatus.NameResolutionFailure: return "store_unreachable.dns";
                case WebExceptionStatus.Timeout: return "store_unreachable.timeout";
                case WebExceptionStatus.TrustFailure:
                case WebExceptionStatus.SecureChannelFailure: return "store_unreachable.tls";
                case WebExceptionStatus.ProxyNameResolutionFailure:
                case WebExceptionStatus.RequestProhibitedByProxy: return "store_unreachable.proxy";
                case WebExceptionStatus.ProtocolError when httpStatus > 0:
                    return "store_unreachable.http_" + httpStatus.ToString(CultureInfo.InvariantCulture);
                default: return "store_unreachable.other";
            }
        }

        // Try again after a failed Store request asks the Store again; a good answer is reused.
        public static bool NeedsFetch(Task? release) => release == null || release.IsFaulted || release.IsCanceled;

        public static bool SameSha(string a, string b) =>
            a.Length == 64 && string.Equals(a, b, StringComparison.OrdinalIgnoreCase);

        // Other versions' downloads in the download folder (kept: the one being installed).
        public static IEnumerable<string> Stale(IEnumerable<string> files, string keepName)
        {
            foreach (var f in files)
            {
                var name = Path.GetFileName(f);
                if (string.Equals(name, keepName, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(name, keepName + ".part", StringComparison.OrdinalIgnoreCase))
                    continue;
                yield return f;
            }
        }

        static string Text(IDictionary<string, object?> d, string key) =>
            d.TryGetValue(key, out var v) && v != null ? (Convert.ToString(v, CultureInfo.InvariantCulture) ?? "").Trim() : "";

        static long Number(IDictionary<string, object?> d, string key)
        {
            if (!d.TryGetValue(key, out var v) || v == null)
                return 0;
            try { return Convert.ToInt64(v, CultureInfo.InvariantCulture); }
            catch (Exception ex) when (ex is FormatException || ex is InvalidCastException || ex is OverflowException) { return 0; }
        }

        static bool IsHex(string s)
        {
            foreach (var c in s)
            {
                if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                    return false;
            }
            return true;
        }
    }
}
