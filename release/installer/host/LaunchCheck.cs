using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace DuckySetup
{
    // After "Open UEFN Ducky" on the Done screen: did the app come up? Setup's window is
    // closed by now; this watches the app's own boot record (frontend/boot_report.py writes
    // %LOCALAPPDATA%\UEFN-Ducky\crash\boot.json) for up to Stats.LaunchWatchSeconds and sends
    // one setup.launch count. A PC whose app never starts can't report it itself, so until
    // this, "installed, never opened" could not be told from "the app didn't start".
    // Reads one small file and checks one process id; the rules are in Stats.cs.
    internal static class LaunchCheck
    {
        static string BootPath => Path.Combine(Engine.AppDataRoot, "crash", "boot.json");

        // Blocks until the start is settled (seconds when it works). Never throws.
        public static void Report(DateTime launchedUtc, string kind)
        {
            try
            {
                if (!Usage.Enabled)
                    return;
                var launchedAt = (launchedUtc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
                var deadline = launchedUtc.AddSeconds(Stats.LaunchWatchSeconds);
                var lastLook = "";
                var since = DateTime.UtcNow;
                var fresh = false;
                var stage = "";
                var verdict = "";
                while (verdict.Length == 0)
                {
                    Thread.Sleep(1000);
                    var now = DateTime.UtcNow;
                    var seen = Read(launchedAt);
                    fresh = seen.fresh;
                    if (seen.fresh && seen.stage.Length > 0)
                        stage = seen.stage;
                    var look = Stats.LaunchLook(seen.fresh, seen.state, seen.alive);
                    if (look != lastLook)
                    {
                        lastLook = look;
                        since = now;
                    }
                    verdict = Stats.LaunchVerdict(look, (now - since).TotalSeconds, fresh, now >= deadline);
                }
                var props = new Dictionary<string, string> { ["outcome"] = verdict, ["kind"] = kind };
                if (verdict == "started")
                    props["took"] = Stats.LaunchTook((DateTime.UtcNow - launchedUtc).TotalSeconds);
                else if (stage.Length > 0)
                    props["stage"] = Stats.Stage(stage);
                Usage.Track("setup.launch", props);
                Usage.SendNow(5000);
            }
            catch (Exception)
            {
                // counts never get in the way
            }
        }

        // The boot record now. fresh: written by this start (or by a UEFN Ducky that was
        // already up and took the start over); alive: the process that wrote it still runs.
        static (bool fresh, string state, bool alive, string stage) Read(double launchedAt)
        {
            Dictionary<string, object>? record;
            try
            {
                // The app rewrites this file as it starts: share it, never lock it.
                using var file = new FileStream(BootPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                using var reader = new StreamReader(file, Encoding.UTF8);
                record = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
            }
            catch (Exception)
            {
                return (false, "", false, "");
            }
            if (record == null)
                return (false, "", false, "");
            var state = record.TryGetValue("state", out var s) ? Convert.ToString(s) ?? "" : "";
            var started = record.TryGetValue("started", out var t) ? Number(t) : 0;
            var alive = record.TryGetValue("pid", out var p) && Alive((int)Number(p));
            var fresh = started >= launchedAt - 5 || (state == "ok" && alive);
            return (fresh, state, alive, LastStage(record));
        }

        static double Number(object? value)
        {
            try { return Convert.ToDouble(value); }
            catch (Exception) { return 0; }
        }

        // stages is [[name, seconds], ...]: the last one is how far the start got.
        static string LastStage(Dictionary<string, object> record)
        {
            if (!record.TryGetValue("stages", out var raw) || !(raw is IList stages) || stages.Count == 0)
                return "";
            return stages[stages.Count - 1] is IList last && last.Count > 0 ? Convert.ToString(last[0]) ?? "" : "";
        }

        static bool Alive(int pid)
        {
            if (pid <= 0)
                return false;
            try
            {
                using var proc = Process.GetProcessById(pid);
                return !proc.HasExited;
            }
            catch (ArgumentException)
            {
                return false; // no such process
            }
            catch (Exception)
            {
                return true; // there, but not ours to inspect
            }
        }
    }
}
