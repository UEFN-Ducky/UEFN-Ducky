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
    // The borderless Ducky window both Setup and the uninstaller use: rounded corners,
    // drop shadow, DPI scaling and one page from ui/ talking to the host through
    // window.chrome.webview messages. The page drags the window and minimizes it; every
    // other message goes to HandleMessage.
    internal abstract class DuckyWindow : Form
    {
        protected readonly WebView2 View = new WebView2 { Dock = DockStyle.Fill };
        protected readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        readonly string _page;

        // Logical size; scaled to the monitor's DPI so the page never gets cramped.
        static readonly Size BaseSize = new Size(720, 500);

        protected DuckyWindow(string title, string page)
        {
            _page = page;
            Text = title;
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false; // the page drags the window; a double-click must not maximize it
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = BaseSize;
            BackColor = Color.FromArgb(10, 10, 10);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch (Exception) { /* keep the default taskbar icon */ }
            Controls.Add(View);
        }

        protected abstract void HandleMessage(string type, Dictionary<string, object> msg);

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
                Engine.LogHostError(ex);
                if (Stats.IsWebView2Missing(ex.GetType().Name + ": " + ex.Message))
                {
                    // Setup's own window needs the runtime too. Tools that remove Microsoft Edge
                    // take it out, and "could not start its window" told nobody what to install.
                    var open = MessageBox.Show(
                        this,
                        Stats.WebView2MissingText,
                        Text,
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Error);
                    if (open == DialogResult.Yes)
                    {
                        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(Stats.WebView2Page) { UseShellExecute = true }); }
                        catch { /* no browser to open it: the address is in the message */ }
                    }
                }
                else
                {
                    MessageBox.Show(
                        this,
                        Text + " could not start its window.\n\n" + ex.Message,
                        Text,
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
                Environment.ExitCode = 1;
                Close();
            }
        }

        async Task InitWeb()
        {
            Engine.ExtractUi();
            var env = await CoreWebView2Environment.CreateAsync(
                null,
                Path.Combine(Engine.WorkRoot, "setup-webview-" + System.Diagnostics.Process.GetCurrentProcess().Id));
            await View.EnsureCoreWebView2Async(env);
            View.DefaultBackgroundColor = Color.FromArgb(255, 10, 10, 10);
            View.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            View.CoreWebView2.Settings.AreDevToolsEnabled = false;
            View.CoreWebView2.Settings.IsStatusBarEnabled = false;
            View.CoreWebView2.WebMessageReceived += OnMessage;
            View.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "ducky.setup",
                Engine.UiDir,
                CoreWebView2HostResourceAccessKind.Allow);
            View.CoreWebView2.Navigate("https://ducky.setup/" + _page);
        }

        void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> msg;
            try { msg = Json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); }
            catch { return; }
            var type = Str(msg, "type");
            if (type == "drag")
            {
                Native.ReleaseCapture();
                Native.SendMessage(Handle, Native.WmNcLButtonDown, Native.HtCaption, 0);
                return;
            }
            if (type == "minimize")
            {
                WindowState = FormWindowState.Minimized;
                return;
            }
            HandleMessage(type, msg);
        }

        protected void Post(object payload)
        {
            if (View.CoreWebView2 == null) return;
            View.CoreWebView2.PostWebMessageAsJson(Json.Serialize(payload));
        }

        protected void OnUi(Action action)
        {
            try { BeginInvoke(action); }
            catch (InvalidOperationException) { /* window already closed */ }
        }

        protected static string Str(Dictionary<string, object> msg, string key)
        {
            return msg.TryGetValue(key, out var v) && v != null ? Convert.ToString(v) ?? "" : "";
        }

        protected static bool Truthy(Dictionary<string, object> msg, string key)
        {
            if (!msg.TryGetValue(key, out var v) || v == null) return false;
            if (v is bool b) return b;
            var s = Convert.ToString(v);
            return s == "True" || s == "true" || s == "1";
        }
    }
}
