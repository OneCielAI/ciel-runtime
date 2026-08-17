export const CIEL_BROWSER_CONTROL_API = "ai.oneciel.cielarvis.browser-control/v1" as const;

export type BrowserTab = {
  id: string;
  label: string;
  url: string;
  title: string;
  loading: boolean;
  visible: boolean;
  frame_id: number;
  popup?: boolean;
};

export type BrowserBounds = { x: number; y: number; width: number; height: number };

export type BrowserPointerInput = {
  action: "move" | "down" | "up" | "click" | "double_click" | "wheel";
  x: number;
  y: number;
  button?: "left" | "middle" | "right" | "none";
  delta_x?: number;
  delta_y?: number;
  modifiers?: readonly ("alt" | "ctrl" | "meta" | "shift")[];
  coordinate_space?: "screenshot" | "css";
  frame_id: number;
};

export type BrowserKeyboardInput =
  | { action: "type"; text: string; frame_id: number }
  | { action: "press" | "down" | "up"; key: string; code?: string; modifiers?: readonly ("alt" | "ctrl" | "meta" | "shift")[]; frame_id: number };

export type BrowserSnapshot = {
  frame_id: number;
  url: string;
  title: string;
  viewport: { width: number; height: number; scrollX: number; scrollY: number; devicePixelRatio: number };
  text: string;
  links: readonly { index: number; text: string; href: string }[];
  controls: readonly {
    index: number;
    tag: string;
    type: string;
    text: string;
    x: number;
    y: number;
    width: number;
    height: number;
    disabled: boolean;
  }[];
};

export type BrowserScreenshot = {
  tab_id: string;
  frame_id: number;
  mime_type: "image/jpeg";
  data: string;
  coordinate_space: "screenshot_pixels";
  viewport: { width: number; height: number; scrollX: number; scrollY: number; devicePixelRatio: number };
};

/**
 * The platform-neutral control plane used by both the visible Browser app and
 * the MCP adapter. Browser engines must not expose host IPC to remote pages.
 */
export interface BrowserController {
  createTab(url?: string): Promise<BrowserTab>;
  listTabs(): Promise<readonly BrowserTab[]>;
  closeTab(tabId: string): Promise<void>;
  activateTab(tabId: string): Promise<BrowserTab>;
  navigate(tabId: string, url: string): Promise<BrowserTab>;
  back(tabId: string): Promise<unknown>;
  forward(tabId: string): Promise<unknown>;
  reload(tabId: string): Promise<void>;
  snapshot(tabId: string): Promise<BrowserSnapshot>;
  evaluate(tabId: string, script: string): Promise<unknown>;
  screenshot(tabId: string): Promise<BrowserScreenshot>;
  pointer(tabId: string, input: BrowserPointerInput): Promise<unknown>;
  keyboard(tabId: string, input: BrowserKeyboardInput): Promise<unknown>;
  setBounds(tabId: string, bounds: BrowserBounds): Promise<void>;
  setVisible(tabId: string, visible: boolean): Promise<void>;
}

export const BROWSER_MCP_TOOLS = [
  "browser_tabs_list", "browser_tab_open", "browser_tab_close", "browser_tab_activate",
  "browser_navigate", "browser_back", "browser_forward", "browser_reload",
  "browser_snapshot", "browser_javascript_evaluate", "browser_screenshot",
  "browser_pointer", "browser_keyboard",
] as const;
