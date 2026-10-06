using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
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

        static string? _extracted;

        // Extracts Setup-engine.exe once per host run and returns the path to
        // run. If an older engine still runs from setup-engine\ and locks the
        // file, this run gets its own run-<pid>\ folder instead of silently
        // reusing (and installing) the old engine.
        public static string ExtractEngine()
        {
            if (_extracted != null)
                return _extracted;
            Directory.CreateDirectory(EngineDir);
            SweepRunDirs();
            var path = EnginePath;
            if (!Embed.ExtractTo("Setup-engine.exe", path))
            {
                path = Path.Combine(EngineDir, "run-" + Process.GetCurrentProcess().Id, ExeName);
                if (!Embed.ExtractTo("Setup-engine.exe", path))
                    throw new InvalidOperationException("Could not write " + path);
            }
            _extracted = path;
            return path;
        }

        // The window needs WebView2 next to the engine; the silent path never does.
        public static void ExtractWebView()
        {
            foreach (var name in new[]
            {
                "WebView2Loader.dll",
                "Microsoft.Web.WebView2.Core.dll",
                "Microsoft.Web.WebView2.WinForms.dll",
            })
            {
                try { Embed.ExtractTo(name, Path.Combine(EngineDir, name)); }
                catch (InvalidOperationException) { /* the window loads the embedded copy */ }
            }
            Native.SetDllDirectory(EngineDir);
        }

        static void SweepRunDirs()
        {
            try
            {
                var mine = "run-" + Process.GetCurrentProcess().Id;
                foreach (var dir in Directory.GetDirectories(EngineDir, "run-*"))
                {
                    if (string.Equals(Path.GetFileName(dir), mine, StringComparison.OrdinalIgnoreCase))
                        continue;
                    try { Directory.Delete(dir, true); }
                    catch { /* that engine is still running */ }
                }
            }
            catch { /* best effort */ }
        }

        public static void LogHostError(Exception ex)
        {
            try
            {
                Directory.CreateDirectory(AppDataRoot);
                File.WriteAllText(Path.Combine(AppDataRoot, "setup-host.log"), ex.ToString());
            }
            catch { /* ignore */ }
        }

        public static void ExtractUi()
        {
            Directory.CreateDirectory(UiDir);
            Embed.ExtractTo("ui/index.html", Path.Combine(UiDir, "index.html"));
            Embed.ExtractTo("ui/styles.css", Path.Combine(UiDir, "styles.css"));
            Embed.ExtractTo("ui/app.js", Path.Combine(UiDir, "app.js"));
        }

        public static bool IsSilent(IReadOnlyList<string> args) => HostArgs.IsSilent(args);

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

        // Silent path (in-app updater): forward the caller's switches untouched,
        // wait for the engine — including the elevated copy it hands off to for
        // /ALLUSERS — and return the engine's own exit code.
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
                Arguments = HostArgs.Join(args),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            var proc = Process.Start(psi)
                ?? throw new InvalidOperationException("Could not start Setup-engine.exe");
            return proc;
        }

        public static int Wait(Process stub, string enginePath)
        {
            var stubPid = stub.Id;
            var probe = new WindowsEngineProbe(enginePath, StartTimeUtc(stub));
            // The stub may also stay alive while its elevated copy installs, so
            // report the hand-off as soon as that copy exists, not only after.
            while (!stub.WaitForExit(250))
            {
                if (_handoffEvent == IntPtr.Zero && EngineWait.PickHandoff(probe.OtherEngines(stubPid)) != null)
                    SignalHandoff();
            }
            return EngineWait.AfterStubExit(stubPid, stub.ExitCode, probe, SignalHandoff);
        }

        static DateTime StartTimeUtc(Process proc)
        {
            try { return proc.StartTime.ToUniversalTime(); }
            catch { return DateTime.UtcNow.AddSeconds(-5); }
        }

        // Named event the in-app updater (1.2.343+) looks for so it can close the
        // panel once UAC was accepted, as it does for the plain Inno Setup. It
        // exists exactly as long as this host process.
        static IntPtr _handoffEvent;

        public static string HandoffEventName(int hostPid) => @"Local\UEFN-Ducky-Setup-Handoff-" + hostPid;

        static void SignalHandoff()
        {
            if (_handoffEvent != IntPtr.Zero)
                return;
            _handoffEvent = Native.CreateEvent(IntPtr.Zero, true, true, HandoffEventName(Process.GetCurrentProcess().Id));
        }

        public static void Cancel()
        {
            foreach (var p in Process.GetProcessesByName("Setup-engine"))
            {
                try { if (!p.HasExited) p.Kill(); }
                catch { /* already gone, or elevated */ }
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
    }

    // Looks at Setup-engine processes through OpenProcess with
    // PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, which Windows grants for
    // an elevated process of the same user. Process.HasExited on .NET Framework
    // asks for more and fails with "Access is denied" on the elevated engine, so
    // the host used to treat a running /ALLUSERS install as finished.
    internal sealed class WindowsEngineProbe : IEngineProbe
    {
        readonly string _enginePath;
        readonly DateTime _stubStartUtc;

        public WindowsEngineProbe(string enginePath, DateTime stubStartUtc)
        {
            _enginePath = Path.GetFullPath(enginePath);
            _stubStartUtc = stubStartUtc;
        }

        public DateTime UtcNow => DateTime.UtcNow;

        public void Sleep(TimeSpan delay) => Thread.Sleep(delay);

        public IReadOnlyList<EngineProc> OtherEngines(int stubPid)
        {
            var found = new List<EngineProc>();
            foreach (var p in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(Engine.ExeName)))
            {
                using (p)
                {
                    if (p.Id == stubPid)
                        continue;
                    var info = Inspect(p.Id);
                    if (info != null)
                        found.Add(info);
                }
            }
            return found;
        }

        EngineProc? Inspect(int pid)
        {
            var h = Native.OpenProcess(Native.ProcessQueryLimitedInformation | Native.Synchronize, false, pid);
            if (h == IntPtr.Zero)
            {
                // Access denied = alive but not ours to look at (elevated as a
                // different admin account). Anything else = already gone.
                return System.Runtime.InteropServices.Marshal.GetLastWin32Error() == Native.ErrorAccessDenied
                    ? new EngineProc { Pid = pid, Inspectable = false }
                    : null;
            }
            try
            {
                var sb = new StringBuilder(1024);
                var size = sb.Capacity;
                var path = Native.QueryFullProcessImageName(h, 0, sb, ref size) ? sb.ToString(0, size) : "";
                var started = Native.GetProcessTimes(h, out var creation, out _, out _, out _)
                    ? DateTime.FromFileTimeUtc(creation)
                    : DateTime.MinValue;
                return new EngineProc
                {
                    Pid = pid,
                    Inspectable = true,
                    SamePath = string.Equals(path, _enginePath, StringComparison.OrdinalIgnoreCase),
                    StartedAfterStub = started >= _stubStartUtc.AddMilliseconds(-50),
                };
            }
            finally
            {
                Native.CloseHandle(h);
            }
        }

        public int? WaitForExit(EngineProc proc)
        {
            if (!proc.Inspectable)
            {
                while (Alive(proc.Pid))
                    Thread.Sleep(400);
                return null;
            }
            var h = Native.OpenProcess(Native.ProcessQueryLimitedInformation | Native.Synchronize, false, proc.Pid);
            if (h == IntPtr.Zero)
                return null;
            try
            {
                Native.WaitForSingleObject(h, Native.Infinite);
                return Native.GetExitCodeProcess(h, out var code) ? unchecked((int)code) : (int?)null;
            }
            finally
            {
                Native.CloseHandle(h);
            }
        }

        static bool Alive(int pid)
        {
            try
            {
                using var p = Process.GetProcessById(pid);
                return !p.HasExited;
            }
            catch (ArgumentException)
            {
                return false;
            }
            catch
            {
                return true; // exists but not inspectable
            }
        }
    }
}
