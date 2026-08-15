import { useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { CielAppManifest, CielWindowDefinition } from "../core/desktopSdk";
import type { DesktopViewport, ManagedWindow, WindowBounds } from "../core/desktopKernel";

type DesktopWindowProps = {
  app: CielAppManifest;
  definition: CielWindowDefinition;
  instance: ManagedWindow;
  active: boolean;
  viewport: DesktopViewport;
  children: ReactNode;
  onFocus: () => void;
  onBounds: (bounds: WindowBounds) => void;
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
};

type PointerOrigin = { pointerX: number; pointerY: number; bounds: WindowBounds };

export function DesktopWindow({ app, definition, instance, active, viewport, children, onFocus, onBounds, onMinimize, onMaximize, onClose }: DesktopWindowProps) {
  const drag = useRef<PointerOrigin | null>(null);
  const resize = useRef<PointerOrigin | null>(null);

  function beginDrag(event: ReactPointerEvent<HTMLElement>) {
    if (instance.mode !== "normal" || (event.target as HTMLElement).closest("button")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerX: event.clientX, pointerY: event.clientY, bounds: { ...instance.bounds } };
    onFocus();
  }

  function moveDrag(event: ReactPointerEvent<HTMLElement>) {
    if (!drag.current) return;
    onBounds({
      ...drag.current.bounds,
      x: drag.current.bounds.x + event.clientX - drag.current.pointerX,
      y: drag.current.bounds.y + event.clientY - drag.current.pointerY,
    });
  }

  function beginResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    resize.current = { pointerX: event.clientX, pointerY: event.clientY, bounds: { ...instance.bounds } };
    onFocus();
  }

  function moveResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resize.current) return;
    onBounds({
      ...resize.current.bounds,
      width: resize.current.bounds.width + event.clientX - resize.current.pointerX,
      height: resize.current.bounds.height + event.clientY - resize.current.pointerY,
    });
  }

  if (instance.mode === "minimized") return null;
  const maximized = instance.mode === "maximized";
  const style = maximized
    ? { left: 8, top: 8, width: Math.max(320, viewport.width - 16), height: Math.max(220, viewport.height - 84), zIndex: instance.zIndex }
    : { left: instance.bounds.x, top: instance.bounds.y, width: instance.bounds.width, height: instance.bounds.height, zIndex: instance.zIndex };

  return (
    <section className={`desktop-window${active ? " active" : ""}${maximized ? " maximized" : ""}`} style={style} onPointerDown={onFocus} data-app-id={app.id}>
      <header className="window-titlebar" onDoubleClick={onMaximize} onPointerDown={beginDrag} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null; }}>
        <span className="window-app-icon" style={{ color: app.icon.accent }}>{app.icon.glyph}</span>
        <div><strong>{instance.title}</strong><small>{app.name} · SDK v1</small></div>
        <nav>
          <button aria-label={`Minimize ${instance.title}`} title="Minimize" onClick={onMinimize}>—</button>
          <button aria-label={`Maximize ${instance.title}`} title={maximized ? "Restore" : "Maximize"} onClick={onMaximize}>{maximized ? "❐" : "□"}</button>
          <button aria-label={`Close ${instance.title}`} title={definition.closable === false ? "Minimize to taskbar" : "Close"} onClick={onClose}>×</button>
        </nav>
      </header>
      <div className="window-content">{children}</div>
      {definition.resizable !== false && !maximized && (
        <div className="window-resize-handle" onPointerDown={beginResize} onPointerMove={moveResize} onPointerUp={() => { resize.current = null; }} />
      )}
    </section>
  );
}
