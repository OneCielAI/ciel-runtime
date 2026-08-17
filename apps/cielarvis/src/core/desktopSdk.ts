export const CIEL_APP_MANIFEST_SCHEMA = "ai.oneciel.cielarvis.app/v1" as const;
export const CIEL_JS_APP_API = "cielarvis-js-app-v1" as const;
export const CIEL_NATIVE_APP_ABI = "cielarvis-native-app-v1" as const;

export type CielAppCapability =
  | "channel.read"
  | "channel.write"
  | "runtime.observe"
  | "terminal.spawn"
  | "terminal.write"
  | "browser.tabs"
  | "browser.navigate"
  | "browser.dom.read"
  | "browser.script.execute"
  | "browser.pointer"
  | "browser.keyboard"
  | "browser.screenshot";

export type CielAppHost =
  | { kind: "builtin"; entrypoint: string }
  | {
      kind: "javascript";
      api: typeof CIEL_JS_APP_API;
      entrypoint: string;
      isolation: "iframe";
      integrity?: string;
    }
  | {
      kind: "native-library";
      abi: typeof CIEL_NATIVE_APP_ABI;
      library: string;
      entrySymbol: string;
    }
  ;

export type CielJsHostMessage = {
  type: "cielarvis.host.initialize";
  api: typeof CIEL_JS_APP_API;
  appId: string;
  capabilities: readonly CielAppCapability[];
};

export type CielJsAppMessage =
  | { type: "cielarvis.app.ready"; api: typeof CIEL_JS_APP_API }
  | { type: "cielarvis.app.error"; message: string };

export type CielWindowPlacement = "center" | "bottom" | "cascade";

export type CielWindowDefinition = {
  id: string;
  title: string;
  singleton?: boolean;
  closable?: boolean;
  resizable?: boolean;
  defaultOpen?: boolean;
  placement: CielWindowPlacement;
  defaultSize: { width: number; height: number };
  minSize: { width: number; height: number };
};

export type CielAppManifest = {
  schema: typeof CIEL_APP_MANIFEST_SCHEMA;
  id: string;
  name: string;
  version: string;
  description: string;
  icon: { glyph: string; accent: string };
  host: CielAppHost;
  capabilities: readonly CielAppCapability[];
  windows: readonly CielWindowDefinition[];
};

export type CielAppPackage = {
  manifest: CielAppManifest;
  provenance: "builtin" | "marketplace" | "development";
  signature?: string;
};

export interface CielAppPackageProvider {
  list(): Promise<readonly CielAppPackage[]>;
}

export interface CielAppInstaller {
  install(appPackage: CielAppPackage): Promise<void>;
  uninstall(appId: string): Promise<void>;
}

export function assertValidManifest(manifest: CielAppManifest): void {
  if (manifest.schema !== CIEL_APP_MANIFEST_SCHEMA) throw new Error(`Unsupported app manifest schema: ${manifest.schema}`);
  if (!/^[a-z0-9]+(?:[.-][a-z0-9]+)+$/.test(manifest.id)) throw new Error(`Invalid app id: ${manifest.id}`);
  if (!manifest.name.trim() || !manifest.version.trim()) throw new Error(`App ${manifest.id} requires a name and version`);
  if (!manifest.windows.length) throw new Error(`App ${manifest.id} does not declare a window`);
  const ids = new Set<string>();
  for (const surface of manifest.windows) {
    if (!surface.id.trim() || ids.has(surface.id)) throw new Error(`App ${manifest.id} has an invalid or duplicate window id`);
    ids.add(surface.id);
    if (surface.defaultSize.width < surface.minSize.width || surface.defaultSize.height < surface.minSize.height) {
      throw new Error(`App ${manifest.id} window ${surface.id} is smaller than its minimum size`);
    }
  }
  if (manifest.host.kind === "native-library" && manifest.host.abi !== CIEL_NATIVE_APP_ABI) {
    throw new Error(`App ${manifest.id} uses an unsupported native ABI`);
  }
  if (manifest.host.kind === "javascript") {
    if (manifest.host.api !== CIEL_JS_APP_API) throw new Error(`App ${manifest.id} uses an unsupported JavaScript API`);
    let protocol = "";
    try {
      protocol = new URL(manifest.host.entrypoint).protocol;
    } catch {
      throw new Error(`App ${manifest.id} has an invalid JavaScript entrypoint`);
    }
    if (protocol !== "https:" && protocol !== "ciel-app:") {
      throw new Error(`App ${manifest.id} JavaScript entrypoint must use https: or ciel-app:`);
    }
  }
}
