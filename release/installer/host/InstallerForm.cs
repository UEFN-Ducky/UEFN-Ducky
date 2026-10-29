using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace DuckySetup
{
    internal sealed class InstallerForm : DuckyWindow
    {
        readonly string _version;
        ProcessHandle? _running;
        System.Windows.Forms.Timer? _poll;
        string _installDir = Engine.UserDir;
        // Web installer only: the Store's latest release, the download's cancel switch,
        // and the downloaded full Setup (deleted once it installed).
        readonly bool _isWeb = Web.IsWeb;
        Task<StoreRelease>? _release;
        System.Threading.CancellationTokenSource? _download;
        string? _downloaded;
        // This run, for the anonymous counts: new PC / same PC again / over an install.
        string _kind = "new";
        string _scope = "user";
        DateTime _installStarted;
        bool _cancelled;
        bool _reported;
        // One more try after a locked file: the switches of this install try.
        string[]? _lastArgs;
        bool _retried;
        bool _closeTracked;
        string _screen = "welcome";

        sealed class ProcessHandle
        {
            public System.Diagnostics.Process Proc = null!;
        }

        public InstallerForm(string version) : base("UEFN Ducky Setup", "index.html")
        {
            _version = version;
        }

        protected override void OnLoad(EventArgs e)
        {
            // Ask the Store which version this installs while the page loads.
            if (_isWeb)
                _release = Task.Run(Web.FetchLatest);
            base.OnLoad(e);
        }

        protected override void HandleMessage(string type, Dictionary<string, object> msg)
        {
            if (type == "ready") { PostInit(); return; }
            if (type == "quit")
            {
                TrackClose(Str(msg, "screen"), false);
                CancelRun();
                Close();
                return;
            }
            if (type == "pickFolder") { PickFolder(Str(msg, "path")); return; }
            if (type == "diskFree") { PostDisk(Str(msg, "path")); return; }
            if (type == "startInstall") { StartInstall(msg); return; }
            if (type == "cancel")
            {
                _cancelled = true;
                CancelRun();
                return;
            }
            if (type == "finish")
            {
                var launch = Truthy(msg, "launch");
                TrackClose("done", launch);
                if (launch)
                    Engine.LaunchApp(_installDir);
                Close();
            }
        }

        async void PostInit()
        {
            var detect = Engine.DetectInstall();
            _installDir = detect.location ?? Engine.UserDir;
            // An id is kept for a new PC only; over an install the app already has one.
            Usage.Load(persist: !detect.installed);
            _kind = Stats.Kind(detect.installed, Usage.HadId);
            Usage.Track("setup.open", new Dictionary<string, string>
            {
                ["kind"] = _kind,
                ["web"] = _isWeb ? "yes" : "no",
                ["os"] = Stats.Os(Environment.OSVersion.Version.Build),
            });
            _ = Usage.Send();
            // The web installer installs whatever the Store has now; no version shows
            // when the Store can't be reached yet (Install then says why).
            var version = _version;
            if (_isWeb)
            {
                try { version = (await _release!).Version; }
                catch (Exception) { version = ""; }
            }
            Post(new
            {
                type = "init",
                version,
                previousVersion = detect.installed ? Engine.InstalledVersion() : "",
                license = Embed.ReadText("LICENSE"),
                isUpgrade = detect.installed,
                allUsers = detect.allUsers,
                dir = _installDir,
                userDir = Engine.UserDir,
                machineDir = Engine.MachineDir,
            });
            PostDisk(_installDir);
        }

        void PickFolder(string current)
        {
            using var dlg = new FolderBrowserDialog
            {
                Description = "Where should UEFN Ducky be installed?",
                SelectedPath = Directory.Exists(current) ? current : Engine.UserDir,
                ShowNewFolderButton = true,
            };
            if (dlg.ShowDialog(this) == DialogResult.OK)
                Post(new { type = "folderPicked", path = dlg.SelectedPath });
        }

        void PostDisk(string path)
        {
            var info = Engine.DiskFree(path);
            if (info == null)
            {
                Post(new { type = "diskFree", text = "", low = false });
                return;
            }
            // The installed app is about 400 MB; the web installer also keeps its download until done.
            var needMb = _isWeb ? 650UL : 450UL;
            var low = info.Value.free < needMb * 1024 * 1024;
            var text = Engine.FormatBytes(info.Value.free) + " free on this drive";
            if (low) text += " — Setup needs about " + needMb + " MB";
            Post(new { type = "diskFree", text, low });
        }

        void StartInstall(Dictionary<string, object> msg)
        {
            var dir = Str(msg, "dir");
            if (dir.Length == 0) dir = Engine.UserDir;
            _installDir = dir;
            var allUsers = Truthy(msg, "allUsers");
            var desktop = Truthy(msg, "desktopIcon");
            var isUpgrade = Truthy(msg, "isUpgrade");
            _scope = allUsers ? "machine" : "user";
            // Try again on the error screen: straight back to the Store request and download.
            if (Stats.IsRetry(_screen))
                Usage.Track("setup.step", new Dictionary<string, string> { ["step"] = "retry" });
            _screen = "progress";
            _installStarted = DateTime.UtcNow;
            _cancelled = false;
            _reported = false;
            Usage.Track("setup.install", new Dictionary<string, string>
            {
                ["kind"] = _kind,
                ["scope"] = _scope,
                ["web"] = _isWeb ? "yes" : "no",
            });
            Engine.ClearProgress();
            var args = Engine.InteractiveArgs(
                isUpgrade ? "" : dir,
                allUsers,
                desktop,
                nolaunch: true,
                setTasks: !isUpgrade);
            _lastArgs = args;
            _retried = false;
            if (_isWeb)
            {
                StartWebInstall(args);
                return;
            }
            RunEngine(args);
        }

        // The full Setup's own engine, followed until it (and its elevated copy) ends.
        void RunEngine(string[] args)
        {
            System.Diagnostics.Process proc;
            string path;
            try
            {
                path = Engine.ExtractEngine();
                proc = Engine.Start(path, args);
                _running = new ProcessHandle { Proc = proc };
            }
            catch (Exception ex)
            {
                Report(false, "engine_start");
                Post(new { type = "installDone", ok = false, error = ex.Message });
                return;
            }
            _poll = new System.Windows.Forms.Timer { Interval = 250 };
            _poll.Tick += (_, __) => PostProgress();
            _poll.Start();
            // Same wait as the silent path: it follows the elevated engine after
            // UAC Yes, so "Done" never shows while files are still being copied.
            Task.Run(() => Engine.Wait(proc, path)).ContinueWith(t =>
            {
                var code = t.IsFaulted ? 1 : t.Result;
                OnUi(() => FinishInstall(code));
            });
        }

        // Web installer: download and check the Store's full Setup, then run it silently
        // with the same switches and show its engine's progress after the download's.
        async void StartWebInstall(string[] args)
        {
            var cancel = new System.Threading.CancellationTokenSource();
            _download = cancel;
            string path;
            try
            {
                Post(new { type = "progress", percent = 0, status = "Connecting to the UEFN Ducky Store…" });
                if (WebRelease.NeedsFetch(_release))
                    _release = Task.Run(Web.FetchLatest);
                var release = await _release!;
                path = await Task.Run(() => Web.Download(release, (got, total) => OnUi(() =>
                {
                    var status = got >= total ? "Checking the download…" : WebRelease.DownloadStatus(got, total);
                    Post(new { type = "progress", percent = WebRelease.DownloadPercent(got, total), status });
                }), cancel.Token));
            }
            catch (Exception ex)
            {
                _download = null;
                var cancelled = ex is OperationCanceledException || cancel.IsCancellationRequested;
                var error = cancelled
                    ? "Setup was cancelled."
                    : ex is InvalidOperationException || ex is UnauthorizedAccessException || ex is IOException
                        ? ex.Message
                        : "Setup couldn't download UEFN Ducky. Check your internet connection and try again.";
                Report(false, cancelled ? "cancelled" : ex is SetupFailure f ? f.Code : "download_failed");
                Post(new { type = "installDone", ok = false, error });
                return;
            }
            _download = null;
            if (cancel.IsCancellationRequested)
            {
                Report(false, "cancelled");
                Post(new { type = "installDone", ok = false, error = "Setup was cancelled." });
                return;
            }
            _downloaded = path;
            RunDownloaded(path, args);
        }

        // The downloaded Setup waits for its engine itself and exits with the engine's code.
        void RunDownloaded(string path, string[] args)
        {
            System.Diagnostics.Process proc;
            try
            {
                proc = Web.StartSetup(path, args);
                _running = new ProcessHandle { Proc = proc };
            }
            catch (Exception ex)
            {
                Report(false, "engine_start");
                Post(new { type = "installDone", ok = false, error = ex.Message });
                return;
            }
            Post(new { type = "progress", percent = WebRelease.DownloadShare, status = "Installing…" });
            _poll = new System.Windows.Forms.Timer { Interval = 250 };
            _poll.Tick += (_, __) => PostProgress();
            _poll.Start();
            // The downloaded Setup waits for its engine (and the elevated copy after
            // UAC Yes) itself and exits with the engine's exit code.
            _ = Task.Run(() =>
            {
                proc.WaitForExit();
                return proc.ExitCode;
            }).ContinueWith(t => OnUi(() => FinishInstall(t.IsFaulted ? 1 : t.Result)));
        }

        // Stops a download, the downloaded Setup and its engine.
        void CancelRun()
        {
            _download?.Cancel();
            if (_isWeb && _running != null)
            {
                try { if (!_running.Proc.HasExited) _running.Proc.Kill(); }
                catch (Exception) { /* already gone */ }
            }
            Engine.Cancel();
        }

        void PostProgress()
        {
            var progress = Engine.ReadProgress();
            if (progress == null)
                return;
            var percent = _isWeb ? WebRelease.InstallPercent(progress.Value.percent) : progress.Value.percent;
            Post(new { type = "progress", percent, status = progress.Value.status });
        }

        void FinishInstall(int code)
        {
            _poll?.Stop();
            PostProgress();
            _running = null;
            var reason = code == 0 || _cancelled ? "" : Stats.FailReason(Engine.ReadLogTail());
            // A running UEFN Ducky the engine could not close kept a file locked: close it, try once more.
            if (reason == "file_in_use" && !_retried && _lastArgs != null)
            {
                _retried = true;
                Post(new { type = "progress", percent = 0, status = "Closing UEFN Ducky and trying again…" });
                Engine.CloseApp();
                Engine.ClearProgress();
                if (_isWeb && _downloaded != null)
                    RunDownloaded(_downloaded, _lastArgs);
                else
                    RunEngine(_lastArgs);
                return;
            }
            Report(code == 0, _cancelled ? "cancelled" : Stats.FailCode(code, reason));
            if (code == 0 && _isWeb)
            {
                Web.Cleanup(_downloaded);
                _downloaded = null;
            }
            if (code == 0)
                Post(new { type = "installDone", ok = true });
            else
                Post(new
                {
                    type = "installDone",
                    ok = false,
                    error = "Installer did not finish. If Windows asked for permission, choose Yes and try again.",
                });
        }

        // How one install try ended, counted once per try.
        void Report(bool ok, string code)
        {
            if (_reported)
                return;
            _reported = true;
            _screen = ok ? "done" : "error";
            Usage.Track("setup.result", new Dictionary<string, string>
            {
                ["outcome"] = Stats.Outcome(ok, _cancelled || code == "cancelled"),
                ["code"] = code,
                ["kind"] = _kind,
                ["web"] = _isWeb ? "yes" : "no",
                ["took"] = Stats.TookBucket((DateTime.UtcNow - _installStarted).TotalSeconds),
            });
            _ = Usage.Send();
        }

        // Which screen Setup was closed on, and whether "Open UEFN Ducky" was pressed.
        void TrackClose(string screen, bool launch)
        {
            if (_closeTracked)
                return;
            _closeTracked = true;
            Usage.Track("setup.close", new Dictionary<string, string>
            {
                ["screen"] = Stats.Screen(screen),
                ["launch"] = launch ? "yes" : "no",
            });
            Usage.SendNow(2000);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_running != null || _download != null)
            {
                _cancelled = true;
                Report(false, "cancelled");
                CancelRun();
            }
            _poll?.Stop();
            TrackClose(_screen, false);
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            _poll?.Stop();
            base.OnFormClosed(e);
        }
    }
}
