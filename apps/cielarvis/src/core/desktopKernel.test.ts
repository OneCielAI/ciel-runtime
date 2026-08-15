import { describe, expect, it } from "vitest";
import { CIEL_APP_MANIFEST_SCHEMA, CIEL_JS_APP_API, CIEL_NATIVE_APP_ABI, assertValidManifest, type CielAppManifest } from "./desktopSdk";
import { CielDesktopKernel } from "./desktopKernel";

const manifest: CielAppManifest = {
  schema: CIEL_APP_MANIFEST_SCHEMA,
  id: "ai.oneciel.test",
  name: "Test",
  version: "1.0.0",
  description: "test app",
  icon: { glyph: "T", accent: "#fff" },
  host: { kind: "builtin", entrypoint: "builtin://test" },
  capabilities: [],
  windows: [{ id: "main", title: "Test", singleton: true, placement: "center", defaultSize: { width: 600, height: 400 }, minSize: { width: 300, height: 200 } }],
};

describe("CielDesktopKernel", () => {
  it("registers manifests and manages a singleton window lifecycle", () => {
    const kernel = new CielDesktopKernel([{ manifest, provenance: "builtin" }]);
    const viewport = { width: 1200, height: 800 };
    const id = kernel.openApp(manifest.id, viewport);
    expect(kernel.openApp(manifest.id, viewport)).toBe(id);
    expect(kernel.getSnapshot().windows).toHaveLength(1);
    kernel.minimize(id);
    expect(kernel.getSnapshot().windows[0].mode).toBe("minimized");
    kernel.activateApp(manifest.id, viewport);
    expect(kernel.getSnapshot().windows[0].mode).toBe("normal");
    kernel.toggleMaximize(id, viewport);
    expect(kernel.getSnapshot().windows[0].mode).toBe("maximized");
  });

  it("returns and retains the constrained bounds after a resize commit", () => {
    const kernel = new CielDesktopKernel([{ manifest, provenance: "builtin" }]);
    const viewport = { width: 1200, height: 800 };
    const id = kernel.openApp(manifest.id, viewport);
    const committed = kernel.updateBounds(id, { x: 48, y: 56, width: 920, height: 640 }, viewport);
    expect(committed).toEqual({ x: 48, y: 56, width: 920, height: 640 });
    expect(kernel.getSnapshot().windows[0].bounds).toEqual(committed);
  });

  it("defines the future native-library boundary without accepting another ABI", () => {
    expect(() => assertValidManifest({ ...manifest, host: { kind: "native-library", abi: CIEL_NATIVE_APP_ABI, library: "app.dll", entrySymbol: "cielarvis_app_v1" } })).not.toThrow();
    expect(() => assertValidManifest({ ...manifest, host: { kind: "native-library", abi: "wrong" as typeof CIEL_NATIVE_APP_ABI, library: "app.dll", entrySymbol: "entry" } })).toThrow(/unsupported native ABI/i);
  });

  it("accepts sandboxed JavaScript apps and rejects unsafe entrypoints", () => {
    const portable: CielAppManifest = {
      ...manifest,
      id: "ai.oneciel.portable",
      host: { kind: "javascript", api: CIEL_JS_APP_API, entrypoint: "https://apps.example.test/index.html", isolation: "iframe" },
    };
    expect(() => assertValidManifest(portable)).not.toThrow();
    expect(() => assertValidManifest({
      ...portable,
      id: "ai.oneciel.unsafe",
      host: { kind: "javascript", api: CIEL_JS_APP_API, entrypoint: "file:///secrets.html", isolation: "iframe" },
    })).toThrow(/https: or ciel-app:/);
  });
});
