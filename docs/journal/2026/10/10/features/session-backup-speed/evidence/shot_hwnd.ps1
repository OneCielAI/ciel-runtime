param([long]$Handle, [string]$Out)
# Captures one window by handle (PrintWindow), even when covered, without focusing it.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W5 {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
[W5]::SetProcessDPIAware() | Out-Null
$h = [IntPtr]$Handle
$r = New-Object W5+RECT; [W5]::GetWindowRect($h, [ref]$r) | Out-Null
if (($r.R - $r.L) -le 0) { "window $Handle not found"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc(); $ok = [W5]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); "saved $Out printwindow=$ok"
