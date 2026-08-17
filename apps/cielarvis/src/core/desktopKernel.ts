import type { CielAppManifest, CielAppPackage, CielWindowDefinition } from "./desktopSdk";
import { assertValidManifest } from "./desktopSdk";

export type DesktopViewport = { width: number; height: number };
export type WindowBounds = { x: number; y: number; width: number; height: number };
export type WindowMode = "normal" | "minimized" | "maximized";
export type WindowPlacementOptions = { avoidAppIds?: readonly string[]; gap?: number };

export type ManagedWindow = {
  id: string;
  appId: string;
  surfaceId: string;
  title: string;
  bounds: WindowBounds;
  restoreBounds?: WindowBounds;
  mode: WindowMode;
  zIndex: number;
};

export type DesktopSnapshot = {
  revision: number;
  apps: readonly CielAppManifest[];
  windows: readonly ManagedWindow[];
  activeWindowId: string | null;
};

function boundedSize(definition: CielWindowDefinition, viewport: DesktopViewport) {
  return {
    width: Math.max(definition.minSize.width, Math.min(definition.defaultSize.width, Math.max(definition.minSize.width, viewport.width - 24))),
    height: Math.max(definition.minSize.height, Math.min(definition.defaultSize.height, Math.max(definition.minSize.height, viewport.height - 24))),
  };
}

function initialBounds(definition: CielWindowDefinition, viewport: DesktopViewport, cascade: number): WindowBounds {
  const size = boundedSize(definition, viewport);
  if (definition.placement === "bottom") {
    return {
      x: Math.max(12, Math.round((viewport.width - size.width) / 2)),
      y: Math.max(12, viewport.height - size.height - 78),
      ...size,
    };
  }
  const offset = definition.placement === "cascade" ? (cascade % 6) * 26 : 0;
  return {
    x: Math.max(12, Math.round((viewport.width - size.width) / 2) + offset),
    y: Math.max(12, Math.round((viewport.height - size.height) / 2) + offset),
    ...size,
  };
}

function constrain(bounds: WindowBounds, viewport: DesktopViewport, minimum: { width: number; height: number }): WindowBounds {
  const width = Math.max(minimum.width, Math.min(bounds.width, Math.max(minimum.width, viewport.width - 12)));
  const height = Math.max(minimum.height, Math.min(bounds.height, Math.max(minimum.height, viewport.height - 12)));
  return {
    x: Math.max(0, Math.min(bounds.x, Math.max(0, viewport.width - 96))),
    y: Math.max(0, Math.min(bounds.y, Math.max(0, viewport.height - 46))),
    width,
    height,
  };
}

function overlapArea(left: WindowBounds, right: WindowBounds): number {
  const width = Math.max(0, Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x));
  const height = Math.max(0, Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y));
  return width * height;
}

function placeAwayFrom(
  bounds: WindowBounds,
  obstacles: readonly WindowBounds[],
  viewport: DesktopViewport,
  gap: number,
): WindowBounds {
  if (!obstacles.some((obstacle) => overlapArea(bounds, obstacle) > 0)) return bounds;
  const maxX = Math.max(12, viewport.width - bounds.width - 12);
  const maxY = Math.max(12, viewport.height - bounds.height - 78);
  const fit = (candidate: WindowBounds): WindowBounds => ({
    ...candidate,
    x: Math.max(12, Math.min(candidate.x, maxX)),
    y: Math.max(12, Math.min(candidate.y, maxY)),
  });
  const candidates = [bounds];
  for (const obstacle of obstacles) {
    candidates.push(
      { ...bounds, x: obstacle.x - bounds.width - gap, y: obstacle.y },
      { ...bounds, x: obstacle.x + obstacle.width + gap, y: obstacle.y },
      { ...bounds, x: obstacle.x, y: obstacle.y - bounds.height - gap },
      { ...bounds, x: obstacle.x, y: obstacle.y + obstacle.height + gap },
    );
  }
  candidates.push(
    { ...bounds, x: 12, y: 12 },
    { ...bounds, x: maxX, y: 12 },
    { ...bounds, x: 12, y: maxY },
    { ...bounds, x: maxX, y: maxY },
  );
  return candidates.map(fit).sort((left, right) => {
    const leftOverlap = obstacles.reduce((total, obstacle) => total + overlapArea(left, obstacle), 0);
    const rightOverlap = obstacles.reduce((total, obstacle) => total + overlapArea(right, obstacle), 0);
    if (leftOverlap !== rightOverlap) return leftOverlap - rightOverlap;
    const leftMove = Math.abs(left.x - bounds.x) + Math.abs(left.y - bounds.y);
    const rightMove = Math.abs(right.x - bounds.x) + Math.abs(right.y - bounds.y);
    return leftMove - rightMove;
  })[0];
}

export class CielDesktopKernel {
  private packages = new Map<string, CielAppPackage>();
  private windows = new Map<string, ManagedWindow>();
  private listeners = new Set<() => void>();
  private activeWindowId: string | null = null;
  private revision = 0;
  private nextWindow = 1;
  private nextZ = 10;
  private current: DesktopSnapshot = { revision: 0, apps: [], windows: [], activeWindowId: null };

  constructor(packages: readonly CielAppPackage[] = []) {
    this.register(packages);
  }

  getSnapshot = (): DesktopSnapshot => this.current;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  register(packages: readonly CielAppPackage[]): void {
    for (const appPackage of packages) {
      assertValidManifest(appPackage.manifest);
      this.packages.set(appPackage.manifest.id, appPackage);
    }
    this.publish();
  }

  openApp(appId: string, viewport: DesktopViewport, surfaceId?: string, placement: WindowPlacementOptions = {}): string {
    const appPackage = this.packages.get(appId);
    if (!appPackage) throw new Error(`App is not registered: ${appId}`);
    const definition = surfaceId
      ? appPackage.manifest.windows.find((candidate) => candidate.id === surfaceId)
      : appPackage.manifest.windows[0];
    if (!definition) throw new Error(`App ${appId} does not provide window ${surfaceId}`);
    const existing = definition.singleton
      ? [...this.windows.values()].find((candidate) => candidate.appId === appId && candidate.surfaceId === definition.id)
      : undefined;
    if (existing) {
      existing.mode = "normal";
      existing.bounds = this.boundsAvoiding(existing.bounds, placement, viewport, existing.id);
      this.focus(existing.id);
      return existing.id;
    }
    const id = `${appId}:${definition.id}:${this.nextWindow++}`;
    const instance: ManagedWindow = {
      id,
      appId,
      surfaceId: definition.id,
      title: definition.title,
      bounds: this.boundsAvoiding(initialBounds(definition, viewport, this.windows.size), placement, viewport),
      mode: "normal",
      zIndex: this.nextZ++,
    };
    this.windows.set(id, instance);
    this.activeWindowId = id;
    this.publish();
    return id;
  }

  focus(id: string): void {
    const instance = this.windows.get(id);
    if (!instance) return;
    if (instance.mode === "minimized") instance.mode = "normal";
    instance.zIndex = this.nextZ++;
    this.activeWindowId = id;
    this.publish();
  }

  activateApp(appId: string, viewport: DesktopViewport): void {
    const instance = [...this.windows.values()].find((candidate) => candidate.appId === appId);
    if (!instance) {
      this.openApp(appId, viewport);
      return;
    }
    if (instance.id === this.activeWindowId && instance.mode !== "minimized") this.minimize(instance.id);
    else this.focus(instance.id);
  }

  minimize(id: string): void {
    const instance = this.windows.get(id);
    if (!instance) return;
    instance.mode = "minimized";
    if (this.activeWindowId === id) this.activeWindowId = this.topVisibleWindow(id)?.id ?? null;
    this.publish();
  }

  minimizeApp(appId: string): void {
    for (const instance of this.windows.values()) if (instance.appId === appId) instance.mode = "minimized";
    if (this.activeWindowId && this.windows.get(this.activeWindowId)?.appId === appId) {
      this.activeWindowId = this.topVisibleWindow()?.id ?? null;
    }
    this.publish();
  }

  restoreApp(appId: string, viewport: DesktopViewport, placement: WindowPlacementOptions = {}): string {
    const instance = [...this.windows.values()].find((candidate) => candidate.appId === appId);
    if (!instance) return this.openApp(appId, viewport, undefined, placement);
    instance.bounds = this.boundsAvoiding(instance.bounds, placement, viewport, instance.id);
    this.focus(instance.id);
    return instance.id;
  }

  toggleMaximize(id: string, viewport: DesktopViewport): void {
    const instance = this.windows.get(id);
    if (!instance) return;
    if (instance.mode === "maximized") {
      instance.mode = "normal";
      if (instance.restoreBounds) instance.bounds = instance.restoreBounds;
      delete instance.restoreBounds;
    } else {
      instance.restoreBounds = { ...instance.bounds };
      instance.mode = "maximized";
      instance.bounds = { x: 8, y: 8, width: Math.max(320, viewport.width - 16), height: Math.max(220, viewport.height - 84) };
    }
    this.focus(id);
  }

  updateBounds(id: string, bounds: WindowBounds, viewport: DesktopViewport): WindowBounds | undefined {
    const instance = this.windows.get(id);
    if (!instance || instance.mode !== "normal") return undefined;
    const definition = this.definition(instance);
    instance.bounds = constrain(bounds, viewport, definition.minSize);
    this.publish();
    return { ...instance.bounds };
  }

  close(id: string): void {
    const instance = this.windows.get(id);
    if (!instance) return;
    if (this.definition(instance).closable === false) {
      this.minimize(id);
      return;
    }
    this.windows.delete(id);
    if (this.activeWindowId === id) this.activeWindowId = this.topVisibleWindow()?.id ?? null;
    this.publish();
  }

  definition(instance: ManagedWindow): CielWindowDefinition {
    const definition = this.packages.get(instance.appId)?.manifest.windows.find((candidate) => candidate.id === instance.surfaceId);
    if (!definition) throw new Error(`Window definition is unavailable: ${instance.appId}/${instance.surfaceId}`);
    return definition;
  }

  private boundsAvoiding(bounds: WindowBounds, placement: WindowPlacementOptions, viewport: DesktopViewport, except?: string): WindowBounds {
    const avoid = new Set(placement.avoidAppIds ?? []);
    if (!avoid.size) return bounds;
    const obstacles = [...this.windows.values()]
      .filter((candidate) => candidate.id !== except && candidate.mode !== "minimized" && avoid.has(candidate.appId))
      .map((candidate) => candidate.bounds);
    return placeAwayFrom(bounds, obstacles, viewport, Math.max(0, placement.gap ?? 16));
  }

  private topVisibleWindow(except?: string): ManagedWindow | undefined {
    return [...this.windows.values()]
      .filter((candidate) => candidate.id !== except && candidate.mode !== "minimized")
      .sort((left, right) => right.zIndex - left.zIndex)[0];
  }

  private publish(): void {
    this.current = {
      revision: ++this.revision,
      apps: [...this.packages.values()].map((value) => value.manifest),
      windows: [...this.windows.values()].map((value) => ({ ...value, bounds: { ...value.bounds } })).sort((left, right) => left.zIndex - right.zIndex),
      activeWindowId: this.activeWindowId,
    };
    for (const listener of this.listeners) listener();
  }
}
