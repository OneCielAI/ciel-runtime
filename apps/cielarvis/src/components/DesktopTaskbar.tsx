import type { CielAppManifest } from "../core/desktopSdk";
import type { ManagedWindow } from "../core/desktopKernel";

export function DesktopTaskbar({ apps, windows, activeWindowId, onActivate }: {
  apps: readonly CielAppManifest[];
  windows: readonly ManagedWindow[];
  activeWindowId: string | null;
  onActivate: (appId: string) => void;
}) {
  return (
    <nav className="desktop-taskbar" aria-label="Cielarvis applications">
      <span className="taskbar-home">CIEL</span>
      <div className="taskbar-apps">
        {apps.map((app) => {
          const instances = windows.filter((instance) => instance.appId === app.id);
          const active = instances.some((instance) => instance.id === activeWindowId && instance.mode !== "minimized");
          const running = instances.length > 0;
          return (
            <button key={app.id} className={active ? "active" : ""} data-running={running} onClick={() => onActivate(app.id)} title={`${app.name} — ${app.description}`}>
              <span style={{ color: app.icon.accent }}>{app.icon.glyph}</span>
              <small>{app.name}</small>
              <i />
            </button>
          );
        })}
      </div>
      <time>{new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
    </nav>
  );
}
