using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

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
    }
}
