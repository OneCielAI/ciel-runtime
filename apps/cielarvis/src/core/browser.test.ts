import { describe, expect, it } from "vitest";
import { BROWSER_MCP_TOOLS, CIEL_BROWSER_CONTROL_API, type BrowserController } from "./browser";
import { BROWSER_APP_ID, BUILTIN_APPS } from "../apps/builtinApps";

describe("Ciel Browser control contract", () => {
  it("registers an isolated standard desktop application with complete control capabilities", () => {
    const browser = BUILTIN_APPS.find((candidate) => candidate.manifest.id === BROWSER_APP_ID)?.manifest;
    expect(browser).toBeDefined();
    expect(browser?.host).toEqual({ kind: "builtin", entrypoint: "builtin://ciel-browser" });
    expect(browser?.capabilities).toEqual(expect.arrayContaining([
      "browser.navigate",
      "browser.dom.read",
      "browser.script.execute",
      "browser.pointer",
      "browser.keyboard",
      "browser.screenshot",
    ]));
    expect(browser?.capabilities).not.toContain("channel.read");
    expect(browser?.capabilities).not.toContain("channel.write");
  });

  it("exposes every visible automation primitive as a stable MCP tool name", () => {
    expect(CIEL_BROWSER_CONTROL_API).toBe("ai.oneciel.cielarvis.browser-control/v1");
    expect(BROWSER_MCP_TOOLS).toEqual(expect.arrayContaining([
      "browser_snapshot",
      "browser_javascript_evaluate",
      "browser_screenshot",
      "browser_pointer",
      "browser_keyboard",
    ]));
  });

  it("keeps the controller interface independent of a concrete WebView engine", () => {
    const implementation: BrowserController = {
      createTab: async () => ({ id: "tab", label: "engine-tab", url: "https://example.com/", title: "Example", loading: false, visible: true, frame_id: 1 }),
      listTabs: async () => [], closeTab: async () => undefined, activateTab: async () => implementation.createTab(),
      navigate: async () => implementation.createTab(), back: async () => undefined, forward: async () => undefined,
      reload: async () => undefined, snapshot: async () => ({ frame_id: 1, url: "https://example.com/", title: "Example", viewport: { width: 1, height: 1, scrollX: 0, scrollY: 0, devicePixelRatio: 1 }, text: "", links: [], controls: [] }),
      evaluate: async () => undefined, screenshot: async () => ({ tab_id: "tab", frame_id: 1, mime_type: "image/jpeg", data: "", coordinate_space: "screenshot_pixels", viewport: { width: 1, height: 1, scrollX: 0, scrollY: 0, devicePixelRatio: 1 } }),
      pointer: async () => undefined, keyboard: async () => undefined, setBounds: async () => undefined, setVisible: async () => undefined,
    };
    expect(implementation).toBeTruthy();
  });
});
