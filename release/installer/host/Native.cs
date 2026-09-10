using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace DuckySetup
{
    internal static class Native
    {
        public const int WmNcLButtonDown = 0xA1;
        public const int HtCaption = 2;

        [DllImport("user32.dll")]
        public static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool SetDllDirectory(string lpPathName);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool GetDiskFreeSpaceEx(string lpDirectoryName, out ulong freeBytesAvailable, out ulong totalNumberOfBytes, out ulong totalNumberOfFreeBytes);
    }

    internal static class Embed
    {
        public static readonly Assembly Asm = Assembly.GetExecutingAssembly();

        public static Stream? Open(string logicalName)
        {
            return Asm.GetManifestResourceStream(logicalName);
        }

        public static void ExtractTo(string logicalName, string dest)
        {
            using var src = Open(logicalName)
                ?? throw new InvalidOperationException("Missing embedded resource: " + logicalName);
            var dir = Path.GetDirectoryName(dest);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);
            if (File.Exists(dest) && new FileInfo(dest).Length == src.Length)
                return;
            var tmp = dest + ".tmp";
            using (var fs = File.Create(tmp))
                src.CopyTo(fs);
            if (File.Exists(dest))
            {
                try { File.Delete(dest); }
                catch (IOException) { try { File.Delete(tmp); } catch { } return; }
                catch (UnauthorizedAccessException) { try { File.Delete(tmp); } catch { } return; }
            }
            File.Move(tmp, dest);
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
