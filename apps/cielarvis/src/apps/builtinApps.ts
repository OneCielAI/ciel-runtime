import { CIEL_APP_MANIFEST_SCHEMA, type CielAppPackage } from "../core/desktopSdk";

export const RUNTIME_APP_ID = "ai.oneciel.cielarvis.runtime";
export const CHAT_APP_ID = "ai.oneciel.cielarvis.chat";

export const BUILTIN_APPS: readonly CielAppPackage[] = [
  {
    provenance: "builtin",
    manifest: {
      schema: CIEL_APP_MANIFEST_SCHEMA,
      id: RUNTIME_APP_ID,
      name: "Ciel Runtime",
      version: "1.0.0",
      description: "Supervised Ciel Runtime terminal multiplexer",
      icon: { glyph: "C", accent: "#63f5c5" },
      host: { kind: "builtin", entrypoint: "builtin://ciel-runtime-terminal" },
      capabilities: ["runtime.observe", "terminal.spawn", "terminal.write"],
      windows: [{
        id: "terminal",
        title: "Ciel Runtime",
        singleton: true,
        closable: false,
        resizable: true,
        defaultOpen: true,
        placement: "center",
        defaultSize: { width: 1080, height: 620 },
        minSize: { width: 620, height: 360 },
      }],
    },
  },
  {
    provenance: "builtin",
    manifest: {
      schema: CIEL_APP_MANIFEST_SCHEMA,
      id: CHAT_APP_ID,
      name: "Ciel Chat",
      version: "1.0.0",
      description: "Structured channel chat for the active Ciel agent",
      icon: { glyph: "✦", accent: "#55bcd0" },
      host: { kind: "builtin", entrypoint: "builtin://ciel-chat" },
      capabilities: ["channel.read", "channel.write"],
      windows: [{
        id: "chat",
        title: "Ciel Chat",
        singleton: true,
        closable: false,
        resizable: true,
        defaultOpen: true,
        placement: "bottom",
        defaultSize: { width: 940, height: 300 },
        minSize: { width: 460, height: 190 },
      }],
    },
  },
];
