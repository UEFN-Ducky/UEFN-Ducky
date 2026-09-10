using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace DuckySetup
{
    internal static class Engine
    {
        // Must NOT start with UEFN-Ducky — panel process sweep kills that prefix.
        public const string ExeName = "Setup-engine.exe";
        public const string AppExeName = "UEFN-Ducky.exe";
        public const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\{EAD694ED-E221-40B0-909B-AFFD7F683C9E}_is1";

        public static string AppDataRoot =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "UEFN-Ducky");

        public static string EngineDir => Path.Combine(AppDataRoot, "setup-engine");
        public static string UiDir => Path.Combine(AppDataRoot, "setup-ui");
        public static string ProgressPath => Path.Combine(AppDataRoot, "setup-progress.txt");
        public static string EnginePath => Path.Combine(EngineDir, ExeName);

        public static string UserDir =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "UEFN Ducky");

        public static string MachineDir =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "UEFN Ducky");

        public static string ExtractEngine()
        {
            Directory.CreateDirectory(EngineDir);
            Embed.ExtractTo("Setup-engine.exe", EnginePath);
            foreach (var name in new[]
            {
                "WebView2Loader.dll",
                "Microsoft.Web.WebView2.Core.dll",
                "Microsoft.Web.WebView2.WinForms.dll",
            })
            {
                try { Embed.ExtractTo(name, Path.Combine(EngineDir, name)); }
                catch (InvalidOperationException) { /* silent path does not need WebView2 */ }
            }
            Native.SetDllDirectory(EngineDir);
            return EnginePath;
        }

        public static void ExtractUi()
        {
            Directory.CreateDirectory(UiDir);
            Embed.ExtractTo("ui/index.html", Path.Combine(UiDir, "index.html"));
            Embed.ExtractTo("ui/styles.css", Path.Combine(UiDir, "styles.css"));
            Embed.ExtractTo("ui/app.js", Path.Combine(UiDir, "app.js"));
        }

        public static bool IsSilent(IReadOnlyList<string> args)
        {
            foreach (var a in args)
            {
                if (a.Equals("/SILENT", StringComparison.OrdinalIgnoreCase)
                    || a.Equals("/VERYSILENT", StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        public static string[] InteractiveArgs(string dir, bool allUsers, bool desktopIcon, bool nolaunch, bool setTasks)
        {
            // In-app updater uses the same silent flags. Interactive host always
            // hides the stock wizard and drives progress itself.
            var list = new List<string>
            {
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
                "/FORCECLOSEAPPLICATIONS",
                allUsers ? "/ALLUSERS" : "/CURRENTUSER",
            };
            if (setTasks)
                list.Add(desktopIcon ? "/TASKS=desktopicon" : "/TASKS=!desktopicon");
            if (!string.IsNullOrWhiteSpace(dir))
                list.Add("/DIR=" + dir);
            if (nolaunch)
                list.Add("/NOLAUNCH");
            return list.ToArray();
        }

        public static int Run(string[] args)
        {
            var path = ExtractEngine();
            return Wait(Start(path, args), path);
        }

        public static Process Start(string path, string[] args)
        {
            var psi = new ProcessStartInfo
            {
                FileName = path,
                Arguments = QuoteArgs(args),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            var proc = Process.Start(psi)
                ?? throw new InvalidOperationException("Could not start Setup-engine.exe");
            return proc;
        }

        public static int Wait(Process proc, string enginePath)
        {
            proc.WaitForExit();
            var code = proc.ExitCode;
            var deadline = DateTime.UtcNow.AddSeconds(2);
            while (DateTime.UtcNow < deadline)
            {
                if (EngineRunning(enginePath))
                {
                    while (EngineRunning(enginePath))
                        Thread.Sleep(400);
                    return 0;
                }
                Thread.Sleep(100);
            }
            return code;
        }

        public static bool EngineRunning(string? enginePath = null)
        {
            enginePath = enginePath ?? EnginePath;
            foreach (var p in Process.GetProcessesByName("Setup-engine"))
            {
                try
                {
                    if (!p.HasExited)
                        return true;
                }
                catch { /* access */ }
                finally { p.Dispose(); }
            }
            return false;
        }

        public static void Cancel()
        {
            foreach (var p in Process.GetProcessesByName("Setup-engine"))
            {
                try { if (!p.HasExited) p.Kill(); }
                catch { /* already gone */ }
                finally { p.Dispose(); }
            }
        }

        public static void LaunchApp(string dir)
        {
            var exe = Path.Combine(dir, AppExeName);
            if (!File.Exists(exe))
                return;
            Process.Start(new ProcessStartInfo
            {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"),
                Arguments = "\"" + exe + "\"",
                UseShellExecute = true,
            });
        }

        public static (bool installed, bool allUsers, string? location) DetectInstall()
        {
            var hives = new[]
            {
                (RegistryHive.CurrentUser, false),
                (RegistryHive.LocalMachine, true),
            };
            foreach (var (hive, allUsers) in hives)
            {
                foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
                {
                    try
                    {
                        using var baseKey = RegistryKey.OpenBaseKey(hive, view);
                        using var key = baseKey.OpenSubKey(UninstallKey);
                        var loc = (key?.GetValue("InstallLocation") as string ?? "").Trim();
                        if (loc.Length > 0)
                            return (true, allUsers, loc.TrimEnd('\\'));
                    }
                    catch (Exception)
                    {
                        // missing key / bitness
                    }
                }
            }
            return (false, false, null);
        }

        public static (ulong free, ulong total)? DiskFree(string path)
        {
            try
            {
                var root = Path.GetPathRoot(Path.GetFullPath(path));
                if (string.IsNullOrEmpty(root))
                    return null;
                if (Native.GetDiskFreeSpaceEx(root, out var free, out var total, out _))
                    return (free, total);
            }
            catch { /* bad path */ }
            return null;
        }

        public static string FormatBytes(ulong bytes)
        {
            const double gb = 1024.0 * 1024 * 1024;
            return (bytes / gb).ToString("0.0") + " GB";
        }

        public static void ClearProgress()
        {
            try { if (File.Exists(ProgressPath)) File.Delete(ProgressPath); }
            catch { /* ignore */ }
        }

        public static (int percent, string status)? ReadProgress()
        {
            try
            {
                if (!File.Exists(ProgressPath))
                    return null;
                var text = File.ReadAllText(ProgressPath, Encoding.UTF8);
                var parts = text.Replace("\r", "").Split('\n');
                if (parts.Length == 0 || !int.TryParse(parts[0].Trim(), out var pct))
                    return null;
                var status = parts.Length > 1 ? parts[1].Trim() : "";
                return (pct, status);
            }
            catch
            {
                return null;
            }
        }

        static string QuoteArgs(IEnumerable<string> args)
        {
            return string.Join(" ", args.Select(Quote));
        }

        static string Quote(string a)
        {
            if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
                return a;
            return "\"" + a.Replace("\"", "\\\"") + "\"";
        }
    }
}
