param([string]$Title)
# Restores the named window if it is minimized, without activating it, so that
# PrintWindow captures its content instead of a 160x28 title bar.
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class R5 {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  public static IntPtr Find(string t) { IntPtr found = IntPtr.Zero; EnumWindows((h, p) => { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); if (sb.ToString().Contains(t)) { found = h; return false; } return true; }, IntPtr.Zero); return found; }
}
"@
$h = [R5]::Find($Title)
if ($h -eq [IntPtr]::Zero) { "window '$Title' not found"; exit 1 }
$iconic = [R5]::IsIconic($h)
if ($iconic) { [R5]::ShowWindow($h, 4) | Out-Null; Start-Sleep -Milliseconds 600 }
"handle=$h was_minimized=$iconic"
