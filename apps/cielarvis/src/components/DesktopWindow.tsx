import { useLayoutEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
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
  onBounds: (bounds: WindowBounds) => WindowBounds | void;
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
};

type PointerOrigin = { pointerX: number; pointerY: number; bounds: WindowBounds; latest: WindowBounds };

export function DesktopWindow({ app, definition, instance, active, viewport, children, onFocus, onBounds, onMinimize, onMaximize, onClose }: DesktopWindowProps) {
  const windowRef = useRef<HTMLElement | null>(null);
  const drag = useRef<PointerOrigin | null>(null);
  const resize = useRef<PointerOrigin | null>(null);
  const pendingPaint = useRef<WindowBounds | null>(null);

  useLayoutEffect(() => {
    const pending = pendingPaint.current;
    if (!pending) return;
    const committed = instance.bounds;
    if (committed.x === pending.x && committed.y === pending.y && committed.width === pending.width && committed.height === pending.height) {
      clearPointerPaint();
      pendingPaint.current = null;
    }
  }, [instance.bounds.x, instance.bounds.y, instance.bounds.width, instance.bounds.height]);

  function paintDrag(bounds: WindowBounds, origin: PointerOrigin) {
    const node = windowRef.current;
    if (!node) return;
    node.style.transform = `translate3d(${bounds.x - origin.bounds.x}px, ${bounds.y - origin.bounds.y}px, 0)`;
    node.style.willChange = "transform";
  }

  function paintResize(bounds: WindowBounds) {
    const node = windowRef.current;
    if (!node) return;
    node.style.width = `${bounds.width}px`;
    node.style.height = `${bounds.height}px`;
    node.style.willChange = "width, height";
  }

  function clearPointerPaint() {
    const node = windowRef.current;
    if (!node) return;
    if (drag.current || resize.current) return;
    node.style.transform = "";
    node.style.width = "";
    node.style.height = "";
    node.style.willChange = "";
  }

  function beginDrag(event: ReactPointerEvent<HTMLElement>) {
    if (instance.mode !== "normal" || (event.target as HTMLElement).closest("button")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = { ...instance.bounds };
    drag.current = { pointerX: event.clientX, pointerY: event.clientY, bounds, latest: bounds };
    onFocus();
  }

  function moveDrag(event: ReactPointerEvent<HTMLElement>) {
    const current = drag.current;
    if (!current) return;
    const next = {
      ...current.bounds,
      x: current.bounds.x + event.clientX - current.pointerX,
      y: current.bounds.y + event.clientY - current.pointerY,
    };
    current.latest = next;
    paintDrag(next, current);
  }

  function finishDrag() {
    const current = drag.current;
    if (!current) return;
    const committed = onBounds(current.latest) ?? current.latest;
    pendingPaint.current = committed;
    drag.current = null;
    requestAnimationFrame(() => {
      if (pendingPaint.current) {
        clearPointerPaint();
        pendingPaint.current = null;
      }
    });
  }

  function beginResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = { ...instance.bounds };
    resize.current = { pointerX: event.clientX, pointerY: event.clientY, bounds, latest: bounds };
    onFocus();
  }

  function moveResize(event: ReactPointerEvent<HTMLDivElement>) {
    const current = resize.current;
    if (!current) return;
    const next = {
      ...current.bounds,
      width: current.bounds.width + event.clientX - current.pointerX,
      height: current.bounds.height + event.clientY - current.pointerY,
    };
    current.latest = next;
    paintResize(next);
  }

  function finishResize() {
    const current = resize.current;
    if (!current) return;
    const committed = onBounds(current.latest) ?? current.latest;
    pendingPaint.current = committed;
    resize.current = null;
    requestAnimationFrame(() => {
      if (pendingPaint.current) {
        clearPointerPaint();
        pendingPaint.current = null;
      }
    });
  }

  if (instance.mode === "minimized") return null;
  const maximized = instance.mode === "maximized";
  const style = maximized
    ? { left: 8, top: 8, width: Math.max(320, viewport.width - 16), height: Math.max(220, viewport.height - 84), zIndex: instance.zIndex }
    : { left: instance.bounds.x, top: instance.bounds.y, width: instance.bounds.width, height: instance.bounds.height, zIndex: instance.zIndex };

  return (
    <section ref={windowRef} className={`desktop-window${active ? " active" : ""}${maximized ? " maximized" : ""}`} style={style} onPointerDown={onFocus} data-app-id={app.id}>
      <header className="window-titlebar" onDoubleClick={onMaximize} onPointerDown={beginDrag} onPointerMove={moveDrag} onPointerUp={finishDrag} onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}>
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
        <div className="window-resize-handle" onPointerDown={beginResize} onPointerMove={moveResize} onPointerUp={finishResize} onPointerCancel={finishResize} onLostPointerCapture={finishResize} />
      )}
    </section>
  );
}
