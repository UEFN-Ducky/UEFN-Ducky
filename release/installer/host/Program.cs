using System;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace DuckySetup
{
    internal static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            // Extract WebView2 next to Setup-engine BEFORE Go() is JIT-compiled
            // (Go references InstallerForm, which loads Microsoft.Web.WebView2.WinForms).
            AppDomain.CurrentDomain.AssemblyResolve += ResolveEmbedded;
            try
            {
                Engine.ExtractEngine();
            }
            catch (InvalidOperationException ex)
            {
                MessageBox.Show(ex.Message, "UEFN Ducky Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            return Go(args);
        }

        static int Go(string[] args)
        {
            if (Engine.IsSilent(args))
                return Engine.Run(args);

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new InstallerForm(VersionString()));
            return Environment.ExitCode;
        }

        static string VersionString()
        {
            var info = Assembly.GetExecutingAssembly().GetCustomAttribute<AssemblyInformationalVersionAttribute>();
            return info?.InformationalVersion ?? "0.0.0";
        }

        static Assembly? ResolveEmbedded(object? sender, ResolveEventArgs e)
        {
            var name = new AssemblyName(e.Name).Name;
            if (string.IsNullOrEmpty(name) || name.EndsWith(".resources", StringComparison.OrdinalIgnoreCase))
                return null;
            var onDisk = Path.Combine(Engine.EngineDir, name + ".dll");
            if (File.Exists(onDisk))
                return Assembly.LoadFrom(onDisk);
            using var stream = Embed.Open(name + ".dll");
            if (stream == null)
                return null;
            using var ms = new MemoryStream();
            stream.CopyTo(ms);
            return Assembly.Load(ms.ToArray());
        }
    }
}
