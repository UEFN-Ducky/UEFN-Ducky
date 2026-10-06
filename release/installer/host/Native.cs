using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace DuckySetup
{
    internal static class Native
    {
        public const int WmNcLButtonDown = 0xA1;
        public const int HtCaption = 2;

        // Enough to wait on and read the exit code of an ELEVATED Setup-engine
        // from this unelevated host. .NET Framework's Process.HasExited asks for
        // PROCESS_QUERY_INFORMATION, which Windows refuses across UAC, so the
        // host used to think the elevated engine had already finished.
        public const uint ProcessQueryLimitedInformation = 0x1000;
        public const uint Synchronize = 0x00100000;
        public const uint Infinite = 0xFFFFFFFF;
        public const int ErrorAccessDenied = 5;

        // Windows 11 window corners and the 1px accent border DWM draws around windows.
        public const int DwmwaWindowCornerPreference = 33;
        public const int DwmwcpRound = 2;
        public const int DwmwaBorderColor = 34;
        public const int DwmwaColorNone = unchecked((int)0xFFFFFFFE);

        [DllImport("dwmapi.dll")]
        public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

        [DllImport("user32.dll")]
        public static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool SetDllDirectory(string lpPathName);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool GetDiskFreeSpaceEx(string lpDirectoryName, out ulong freeBytesAvailable, out ulong totalNumberOfBytes, out ulong totalNumberOfFreeBytes);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, int processId);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "QueryFullProcessImageNameW")]
        public static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder exeName, ref int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetProcessTimes(IntPtr process, out long creation, out long exit, out long kernel, out long user);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateEventW")]
        public static extern IntPtr CreateEvent(IntPtr attributes, bool manualReset, bool initialState, string name);
    }

    internal static class Embed
    {
        public static readonly Assembly Asm = Assembly.GetExecutingAssembly();

        public static Stream? Open(string logicalName)
        {
            return Asm.GetManifestResourceStream(logicalName);
        }

        // Writes the embedded file to dest unless dest already holds exactly
        // those bytes. False when dest holds something else and cannot be
        // replaced (a running older engine keeps it locked) — the caller must
        // then NOT run dest. Comparing bytes, not just length: a stale engine of
        // equal size would otherwise install the previous version.
        public static bool ExtractTo(string logicalName, string dest)
        {
            using var src = Open(logicalName)
                ?? throw new InvalidOperationException("Missing embedded resource: " + logicalName);
            var dir = Path.GetDirectoryName(dest);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);
            if (File.Exists(dest) && SameContent(src, dest))
                return true;
            src.Position = 0;
            var tmp = dest + "." + System.Diagnostics.Process.GetCurrentProcess().Id + ".tmp";
            using (var fs = File.Create(tmp))
                src.CopyTo(fs);
            try
            {
                if (File.Exists(dest))
                    File.Delete(dest);
                File.Move(tmp, dest);
                return true;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                try { File.Delete(tmp); } catch { /* ignore */ }
                return false;
            }
        }

        static bool SameContent(Stream src, string path)
        {
            try
            {
                using var disk = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                if (disk.Length != src.Length)
                    return false;
                src.Position = 0;
                var a = new byte[1 << 20];
                var b = new byte[1 << 20];
                while (true)
                {
                    var n = ReadFull(src, a);
                    var m = ReadFull(disk, b);
                    if (n != m)
                        return false;
                    if (n == 0)
                        return true;
                    for (var i = 0; i < n; i++)
                    {
                        if (a[i] != b[i])
                            return false;
                    }
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                return false;
            }
        }

        static int ReadFull(Stream s, byte[] buf)
        {
            var total = 0;
            while (total < buf.Length)
            {
                var n = s.Read(buf, total, buf.Length - total);
                if (n == 0)
                    break;
                total += n;
            }
            return total;
        }

        public static string ReadText(string logicalName)
        {
            using var src = Open(logicalName);
            if (src == null) return "";
            using var reader = new StreamReader(src);
            return reader.ReadToEnd();
        }
    }
}
