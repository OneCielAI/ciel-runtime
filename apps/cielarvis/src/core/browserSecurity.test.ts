import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("Ciel Browser WebView isolation", () => {
  it("grants native commands only to the bundled main WebView", () => {
    const capabilityUrl = new URL("../../src-tauri/capabilities/default.json", import.meta.url);
    const capability = JSON.parse(readFileSync(capabilityUrl, "utf8"));
    expect(capability.webviews).toEqual(["main"]);
    expect(capability.windows).toBeUndefined();
    expect(capability.remote).toBeUndefined();
    expect(capability.permissions).toContain("allow-browser-screenshot");
    expect(capability.permissions).toContain("allow-browser-pointer");
    expect(capability.permissions).toContain("allow-browser-keyboard");
    expect(capability.permissions).toContain("allow-browser-mcp-status");
    expect(capability.permissions).toContain("allow-terminal-list");
  });

  it("moves application commands behind Tauri's generated ACL", () => {
    const buildUrl = new URL("../../src-tauri/build.rs", import.meta.url);
    const buildScript = readFileSync(buildUrl, "utf8");
    expect(buildScript).toContain("AppManifest::new().commands(COMMANDS)");
    expect(buildScript).toContain('"browser_evaluate"');
    expect(buildScript).toContain('"terminal_spawn"');
    expect(buildScript).toContain('"terminal_list"');
    expect(buildScript).toContain('"runtime_send_message"');
  });
});
