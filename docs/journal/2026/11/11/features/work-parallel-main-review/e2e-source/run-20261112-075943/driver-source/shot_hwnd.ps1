param([long]$Handle, [string]$Out)
# Captures one owned window by handle (PrintWindow). A minimized owned probe
# window is restored without activation first, so the frame is not a title bar.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W6 {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
}
"@
[W6]::SetProcessDPIAware() | Out-Null
$h = [IntPtr]$Handle
if ([W6]::IsIconic($h)) { [W6]::ShowWindow($h, 4) | Out-Null; Start-Sleep -Milliseconds 400 }
$r = New-Object W6+RECT; [W6]::GetWindowRect($h, [ref]$r) | Out-Null
if (($r.R - $r.L) -le 200) { "window $Handle not capturable"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc(); $ok = [W6]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); "saved $Out printwindow=$ok"
