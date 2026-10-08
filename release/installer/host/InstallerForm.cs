using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace DuckySetup
{
    internal sealed class InstallerForm : Form
    {
        readonly WebView2 _web = new WebView2 { Dock = DockStyle.Fill };
        readonly JavaScriptSerializer _json = new JavaScriptSerializer();
        readonly string _version;
        ProcessHandle? _running;
        System.Windows.Forms.Timer? _poll;
        string _installDir = Engine.UserDir;

        sealed class ProcessHandle
        {
            public System.Diagnostics.Process Proc = null!;
        }

        // Logical size; scaled to the monitor's DPI so the page never gets cramped.
        static readonly Size BaseSize = new Size(720, 500);

        public InstallerForm(string version)
        {
            _version = version;
            Text = "UEFN Ducky Setup";
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false; // the page drags the window; a double-click must not maximize it
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = BaseSize;
            BackColor = Color.FromArgb(10, 10, 10);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch (Exception) { /* keep the default taskbar icon */ }
            Controls.Add(_web);
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            // No title bar, no border: round the corners and drop DWM's 1px
            // outline (Windows 11; both calls are no-ops on Windows 10).
            var round = Native.DwmwcpRound;
            Native.DwmSetWindowAttribute(Handle, Native.DwmwaWindowCornerPreference, ref round, sizeof(int));
            var none = Native.DwmwaColorNone;
            Native.DwmSetWindowAttribute(Handle, Native.DwmwaBorderColor, ref none, sizeof(int));
        }

        protected override void OnDpiChanged(DpiChangedEventArgs e)
        {
            base.OnDpiChanged(e);
            Bounds = e.SuggestedRectangle;
        }

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ClassStyle |= 0x00020000; // CS_DROPSHADOW
                return cp;
            }
        }

        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            var scale = DeviceDpi / 96f;
            if (scale > 1.01f)
            {
                ClientSize = new Size((int)Math.Round(BaseSize.Width * scale), (int)Math.Round(BaseSize.Height * scale));
                CenterToScreen();
            }
            try
            {
                await InitWeb();
            }
            catch (Exception ex)
            {
                var text = ex.ToString();
                try
                {
                    Directory.CreateDirectory(Engine.AppDataRoot);
                    File.WriteAllText(Path.Combine(Engine.AppDataRoot, "setup-host.log"), text);
                }
                catch { /* ignore */ }
                MessageBox.Show(
                    this,
                    "UEFN Ducky Setup could not start the installer window.\n\n" + ex.Message,
                    "UEFN Ducky Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                Environment.ExitCode = 1;
                Close();
            }
        }

        async Task InitWeb()
        {
            Engine.ExtractUi();
            var env = await CoreWebView2Environment.CreateAsync(
                null,
                Path.Combine(Engine.AppDataRoot, "setup-webview-" + System.Diagnostics.Process.GetCurrentProcess().Id));
            await _web.EnsureCoreWebView2Async(env);
            _web.DefaultBackgroundColor = Color.FromArgb(255, 10, 10, 10);
            _web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            _web.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _web.CoreWebView2.Settings.IsStatusBarEnabled = false;
            _web.CoreWebView2.WebMessageReceived += OnMessage;
            _web.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "ducky.setup",
                Engine.UiDir,
                CoreWebView2HostResourceAccessKind.Allow);
            _web.CoreWebView2.Navigate("https://ducky.setup/index.html");
        }

        void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> msg;
            try { msg = _json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); }
            catch { return; }
            var type = Str(msg, "type");
            if (type == "ready") { PostInit(); return; }
            if (type == "drag")
            {
                Native.ReleaseCapture();
                Native.SendMessage(Handle, Native.WmNcLButtonDown, Native.HtCaption, 0);
                return;
            }
            if (type == "minimize") { WindowState = FormWindowState.Minimized; return; }
            if (type == "quit")
            {
                Engine.Cancel();
                Close();
                return;
            }
            if (type == "pickFolder") { PickFolder(Str(msg, "path")); return; }
            if (type == "diskFree") { PostDisk(Str(msg, "path")); return; }
            if (type == "startInstall") { StartInstall(msg); return; }
            if (type == "cancel") { Engine.Cancel(); return; }
            if (type == "finish")
            {
                if (Truthy(msg, "launch"))
                    Engine.LaunchApp(_installDir);
                Close();
            }
        }

        void PostInit()
        {
            var detect = Engine.DetectInstall();
            _installDir = detect.location ?? Engine.UserDir;
            Post(new
            {
                type = "init",
                version = _version,
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
            var need = 80UL * 1024 * 1024;
            var low = info.Value.free < need;
            var text = Engine.FormatBytes(info.Value.free) + " free on this drive";
            if (low) text += " — Setup needs about 80 MB";
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
            Engine.ClearProgress();
            var args = Engine.InteractiveArgs(
                isUpgrade ? "" : dir,
                allUsers,
                desktop,
                nolaunch: true,
                setTasks: !isUpgrade);
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
                try { BeginInvoke((Action)(() => FinishInstall(code))); }
                catch (InvalidOperationException) { /* window already closed */ }
            });
        }

        void PostProgress()
        {
            var progress = Engine.ReadProgress();
            if (progress != null)
                Post(new { type = "progress", percent = progress.Value.percent, status = progress.Value.status });
        }

        void FinishInstall(int code)
        {
            _poll?.Stop();
            PostProgress();
            _running = null;
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

        void Post(object payload)
        {
            if (_web.CoreWebView2 == null) return;
            _web.CoreWebView2.PostWebMessageAsJson(_json.Serialize(payload));
        }

        static string Str(Dictionary<string, object> msg, string key)
        {
            return msg.TryGetValue(key, out var v) && v != null ? Convert.ToString(v) ?? "" : "";
        }

        static bool Truthy(Dictionary<string, object> msg, string key)
        {
            if (!msg.TryGetValue(key, out var v) || v == null) return false;
            if (v is bool b) return b;
            var s = Convert.ToString(v);
            return s == "True" || s == "true" || s == "1";
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_running != null)
                Engine.Cancel();
            _poll?.Stop();
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            _poll?.Stop();
            base.OnFormClosed(e);
        }
    }
}
