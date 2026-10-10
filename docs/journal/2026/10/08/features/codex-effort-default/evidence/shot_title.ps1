param([string]$Title, [string]$Out)
# Captures only the named window's own content (PrintWindow), even when it is
# covered by other windows, and without bringing it to the foreground.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class W4 {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public static IntPtr Find(string t) { IntPtr found = IntPtr.Zero; EnumWindows((h, p) => { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); if (IsWindowVisible(h) && sb.ToString().Contains(t)) { found = h; return false; } return true; }, IntPtr.Zero); return found; }
}
"@
[W4]::SetProcessDPIAware() | Out-Null
$h = [W4]::Find($Title)
if ($h -eq [IntPtr]::Zero) { "window '$Title' not found"; exit 1 }
$r = New-Object W4+RECT; [W4]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc(); $ok = [W4]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); "saved $Out handle=$h printwindow=$ok"
