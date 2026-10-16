param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference = 'Stop'
$workspace = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$resolvedTarget = [System.IO.Path]::GetFullPath($Target)
if (-not $resolvedTarget.StartsWith($workspace + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Launcher target must be inside this project.'
}
$taskTemp = Join-Path $workspace 'Build\cache\compiler-temp'
[System.IO.Directory]::CreateDirectory($taskTemp) | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$code = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
public static class Launcher {
    [STAThread]
    public static void Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "Python", "pythonw.exe");
        string script = Path.Combine(root, "App", "launch.py");
        try {
            if (!File.Exists(python) || !File.Exists(script))
                throw new Exception("Unpack the whole ZIP before starting DLSSNR.exe.");
            string arguments = "-I -B -X utf8 \"" + script + "\"";
            if (args.Length == 2 && args[0] == "--check")
                arguments += " --check \"" + args[1] + "\"";
            var info = new ProcessStartInfo(python, arguments);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.EnvironmentVariables.Remove("PYTHONHOME");
            info.EnvironmentVariables.Remove("PYTHONPATH");
            Process.Start(info);
        } catch (Exception error) {
            MessageBox.Show(error.Message, "DLSSNR", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
'@
Add-Type -TypeDefinition $code -Language CSharp -ReferencedAssemblies System.Windows.Forms -OutputType WindowsApplication -OutputAssembly (Join-Path $resolvedTarget 'DLSSNR.exe')
