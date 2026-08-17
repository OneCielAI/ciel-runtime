import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { BrowserTab } from "../core/browser";
import { browserMcpStatus, isDesktop, nativeBrowserController, onBrowserEvent, type BrowserMcpStatus } from "../infrastructure/desktopBridge";

type Props = { active: boolean };

export function CielBrowserApp({ active }: Props) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef<BrowserTab[]>([]);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [activeId, setActiveId] = useState("");
  const [address, setAddress] = useState("https://www.google.com/");
  const [notice, setNotice] = useState(isDesktop() ? "Starting isolated renderer…" : "Native browser engine unavailable in web preview");
  const [preview, setPreview] = useState("");
  const [mcp, setMcp] = useState<BrowserMcpStatus>({ ready: false });
  const [showMcp, setShowMcp] = useState(false);

  const replaceTab = useCallback((next: BrowserTab) => {
    if (next.popup) {
      setNotice("Login popup opened in a separate managed browser window");
      return;
    }
    setTabs((current) => {
      const updated = current.some((tab) => tab.id === next.id)
        ? current.map((tab) => tab.id === next.id ? next : tab)
        : [...current, next];
      tabsRef.current = updated;
      return updated;
    });
    if (next.id === activeId) setAddress(next.url);
  }, [activeId]);

  const createTab = useCallback(async (url?: string) => {
    const tab = await nativeBrowserController.createTab(url);
    setTabs((current) => {
      const updated = [...current, tab];
      tabsRef.current = updated;
      return updated;
    });
    setActiveId(tab.id);
    setAddress(tab.url);
    setNotice("Renderer isolated from the CIELARVIS host");
    return tab;
  }, []);

  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void onBrowserEvent((tab) => { if (!cancelled) replaceTab(tab); }).then((stop) => { unlisten = stop; });
    void nativeBrowserController.listTabs().then(async (existing) => {
      if (cancelled) return;
      const embedded = existing.filter((tab) => !tab.popup);
      if (embedded.length) {
        tabsRef.current = [...embedded];
        setTabs([...embedded]);
        setActiveId(embedded[0].id);
        setAddress(embedded[0].url);
      } else {
        await createTab();
      }
    }).catch((error) => setNotice(String(error)));
    return () => { cancelled = true; unlisten?.(); };
  }, [createTab, replaceTab]);

  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    const refresh = () => void browserMcpStatus()
      .then((status) => { if (!cancelled) setMcp(status); })
      .catch((error) => { if (!cancelled) setMcp({ ready: false, error: String(error) }); });
    refresh();
    const timer = window.setInterval(refresh, 1_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useLayoutEffect(() => {
    if (!isDesktop() || !activeId) return;
    let frame = 0;
    let previous = "";
    const synchronize = () => {
      const node = viewportRef.current;
      if (node && active) {
        const rect = node.getBoundingClientRect();
        const next = `${rect.x}:${rect.y}:${rect.width}:${rect.height}`;
        if (next !== previous && rect.width > 1 && rect.height > 1) {
          previous = next;
          void nativeBrowserController.setBounds(activeId, { x: rect.x, y: rect.y, width: rect.width, height: rect.height });
        }
      }
      frame = requestAnimationFrame(synchronize);
    };
    void nativeBrowserController.setVisible(activeId, active);
    if (active) frame = requestAnimationFrame(synchronize);
    return () => {
      cancelAnimationFrame(frame);
      void nativeBrowserController.setVisible(activeId, false).catch(() => undefined);
    };
  }, [active, activeId]);

  async function activate(tab: BrowserTab) {
    const previous = activeId;
    setActiveId(tab.id);
    setAddress(tab.url);
    if (previous && previous !== tab.id) await nativeBrowserController.setVisible(previous, false);
    await nativeBrowserController.activateTab(tab.id);
    await nativeBrowserController.setVisible(tab.id, active);
  }

  async function close(tab: BrowserTab) {
    await nativeBrowserController.closeTab(tab.id);
    const remaining = tabsRef.current.filter((candidate) => candidate.id !== tab.id);
    tabsRef.current = remaining;
    setTabs(remaining);
    if (activeId === tab.id) {
      const next = remaining.at(-1) ?? await createTab();
      await activate(next);
    }
  }

  async function navigate(event: FormEvent) {
    event.preventDefault();
    if (!activeId) return;
    try {
      const tab = await nativeBrowserController.navigate(activeId, address);
      replaceTab(tab);
      setNotice("Navigating…");
    } catch (error) { setNotice(String(error)); }
  }

  async function capture() {
    if (!activeId) return;
    try {
      const shot = await nativeBrowserController.screenshot(activeId);
      setPreview(`data:${shot.mime_type};base64,${shot.data}`);
      setNotice(`MCP-ready screenshot frame ${shot.frame_id}`);
    } catch (error) { setNotice(String(error)); }
  }

  return (
    <section className="browser-window-app">
      <div className="browser-tabs">
        {tabs.map((tab) => (
          <button key={tab.id} className={tab.id === activeId ? "active" : ""} onClick={() => void activate(tab)}>
            <span>{tab.loading ? "◌" : "◎"}</span><em>{tab.title || new URL(tab.url).hostname}</em><i onClick={(event) => { event.stopPropagation(); void close(tab); }}>×</i>
          </button>
        ))}
        <button className="browser-new-tab" title="New tab" onClick={() => void createTab("about:blank")}>＋</button>
      </div>
      <form className="browser-toolbar" onSubmit={navigate}>
        <button type="button" title="Back" onClick={() => activeId && void nativeBrowserController.back(activeId)}>‹</button>
        <button type="button" title="Forward" onClick={() => activeId && void nativeBrowserController.forward(activeId)}>›</button>
        <button type="button" title="Reload" onClick={() => activeId && void nativeBrowserController.reload(activeId)}>↻</button>
        <input aria-label="Address" value={address} onChange={(event) => setAddress(event.target.value)} />
        <button type="submit">GO</button>
        <button type="button" title="Capture MCP screenshot" onClick={() => void capture()}>▣</button>
      </form>
      <div className="browser-renderer" ref={viewportRef}>
        {!isDesktop() && <p>This preview cannot host a secure native WebView.</p>}
      </div>
      <footer>
        <span>{notice}</span>
        <button type="button" className={mcp.ready ? "online" : ""} onClick={() => setShowMcp((shown) => !shown)}>
          {mcp.ready ? "MCP ONLINE" : "MCP STARTING"}
        </button>
      </footer>
      {showMcp && <aside className="browser-mcp-panel">
        <strong>STREAMABLE HTTP MCP</strong>
        {mcp.error && <p>{mcp.error}</p>}
        <label>Endpoint<input readOnly value={mcp.endpoint ?? "Starting…"} onFocus={(event) => event.currentTarget.select()} /></label>
        <label>Authorization<input readOnly type="password" value={mcp.authorization ?? ""} onFocus={(event) => event.currentTarget.select()} /></label>
        <small>Use the full Authorization value as the request header. This token is regenerated on every CIELARVIS start.</small>
      </aside>}
      {preview && <aside className="browser-shot-preview"><button onClick={() => setPreview("")}>×</button><img src={preview} alt="Latest browser screenshot" /></aside>}
    </section>
  );
}
