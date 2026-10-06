using System;
using System.Collections.Generic;
using System.Linq;

namespace DuckySetup
{
    // Pure command-line and wait logic, no WinForms / WebView2 / Win32, so
    // host.tests can compile it on its own.
    internal static class HostArgs
    {
        // Every installed app's updater (1.0.x .. 1.2.342) runs the downloaded
        // Setup with exactly:
        //   /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
        //   /FORCECLOSEAPPLICATIONS (/CURRENTUSER | /ALLUSERS)
        // Any /SILENT or /VERYSILENT means: no host window, forward everything.
        public static bool IsSilent(IEnumerable<string> args)
        {
            foreach (var a in args)
            {
                if (a.Equals("/SILENT", StringComparison.OrdinalIgnoreCase)
                    || a.Equals("/VERYSILENT", StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        // Inno Setup reads its command line with Delphi-style ParamStr rules:
        // a double quote only toggles quoting, there is no backslash escape.
        // So wrap an argument that holds spaces in quotes and drop any quotes
        // inside it (Inno cannot receive a literal quote anyway).
        public static string Quote(string a)
        {
            if (a.Length == 0)
                return "\"\"";
            if (a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
                return a;
            return "\"" + a.Replace("\"", "") + "\"";
        }

        public static string Join(IEnumerable<string> args)
        {
            return string.Join(" ", args.Select(Quote));
        }
    }

    // What the host learns about one Setup-engine process.
    internal sealed class EngineProc
    {
        public int Pid;
        // False when Windows would not let us look at it (another user's
        // elevated engine). Such a process still counts as running.
        public bool Inspectable;
        public bool SamePath;
        public bool StartedAfterStub;
    }

    internal interface IEngineProbe
    {
        // Setup-engine processes other than the stub we started ourselves.
        IReadOnlyList<EngineProc> OtherEngines(int stubPid);
        // Blocks until the process is gone; its exit code, or null when unknown.
        int? WaitForExit(EngineProc proc);
        void Sleep(TimeSpan delay);
        DateTime UtcNow { get; }
    }

    internal static class EngineWait
    {
        // After the stub engine exits, how long to look for the elevated engine
        // it handed off to (UAC Yes). Inno starts that child before the stub
        // exits, so this is only slack for slow process listing.
        public static readonly TimeSpan HandoffGrace = TimeSpan.FromSeconds(2);

        // For /ALLUSERS the stub engine asks for UAC, starts an elevated copy of
        // Setup-engine.exe and exits 0 straight away. Wait for that copy too and
        // return ITS exit code, so the host never reports success (or exits)
        // while the real install is still running.
        public static int AfterStubExit(int stubPid, int stubExitCode, IEngineProbe probe, Action? onHandoff = null)
        {
            var deadline = probe.UtcNow + HandoffGrace;
            while (true)
            {
                var child = PickHandoff(probe.OtherEngines(stubPid));
                if (child != null)
                {
                    onHandoff?.Invoke();
                    var code = probe.WaitForExit(child);
                    return code ?? stubExitCode;
                }
                if (probe.UtcNow >= deadline)
                    return stubExitCode;
                probe.Sleep(TimeSpan.FromMilliseconds(100));
            }
        }

        // Our elevated engine runs the very file we extracted and started after
        // the stub. An engine left over from an earlier run (different file or
        // older) is not ours. One we may not inspect is assumed to be ours.
        public static EngineProc? PickHandoff(IReadOnlyList<EngineProc> engines)
        {
            foreach (var e in engines)
            {
                if (!e.Inspectable || (e.SamePath && e.StartedAfterStub))
                    return e;
            }
            return null;
        }
    }
}
