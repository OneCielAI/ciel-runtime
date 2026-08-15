import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import type { TerminalSessionInfo } from "../core/contracts";
import { killTerminal, onTerminalOutput, resizeTerminal, writeTerminal } from "../infrastructure/desktopBridge";

type TerminalDeckProps = {
  sessions: TerminalSessionInfo[];
  activeId: string;
  onActivate: (id: string) => void;
  onClosed: (id: string) => void;
  variant?: "default" | "boot";
};

type LiveTerminal = { terminal: Terminal; fit: FitAddon };

export function TerminalDeck({ sessions, activeId, onActivate, onClosed, variant = "default" }: TerminalDeckProps) {
  const hosts = useRef(new Map<string, HTMLDivElement>());
  const live = useRef(new Map<string, LiveTerminal>());
  const viewport = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;
    onTerminalOutput(({ id, data }) => live.current.get(id)?.terminal.write(data)).then((stop) => {
      if (disposed) stop();
      else unlisten = stop;
    });
    return () => {
      disposed = true;
      unlisten?.();
      live.current.forEach(({ terminal }) => terminal.dispose());
      live.current.clear();
    };
  }, []);

  useEffect(() => {
    for (const session of sessions) {
      if (live.current.has(session.id)) continue;
      const host = hosts.current.get(session.id);
      if (!host) continue;
      const terminal = new Terminal({
        cursorBlink: true,
        convertEol: true,
        fontFamily: '"Cascadia Mono", "JetBrains Mono", Consolas, monospace',
        fontSize: 13,
        lineHeight: 1.18,
        scrollback: 8000,
        theme: {
          background: "#050807",
          foreground: "#b7d9d0",
          cursor: "#63f5c5",
          selectionBackground: "#225f50aa",
          black: "#08100e",
          brightBlack: "#4f6861",
          green: "#51d6aa",
          brightGreen: "#8dffd9",
          cyan: "#56c7d8",
          yellow: "#e7ba67",
          red: "#ef7f6c",
        },
      });
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(host);
      terminal.onData((data) => void writeTerminal(session.id, data));
      live.current.set(session.id, { terminal, fit });
      requestAnimationFrame(() => {
        fit.fit();
        void resizeTerminal(session.id, terminal.cols, terminal.rows);
      });
    }
    const selected = live.current.get(activeId);
    if (selected) {
      requestAnimationFrame(() => {
        selected.fit.fit();
        selected.terminal.focus();
        void resizeTerminal(activeId, selected.terminal.cols, selected.terminal.rows);
      });
    }
  }, [sessions, activeId]);

  useEffect(() => {
    const fitActiveTerminal = () => {
      const selected = live.current.get(activeId);
      if (!selected) return;
      requestAnimationFrame(() => {
        selected.fit.fit();
        void resizeTerminal(activeId, selected.terminal.cols, selected.terminal.rows);
      });
    };
    window.addEventListener("resize", fitActiveTerminal);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fitActiveTerminal);
    if (viewport.current) observer?.observe(viewport.current);
    return () => {
      window.removeEventListener("resize", fitActiveTerminal);
      observer?.disconnect();
    };
  }, [activeId]);

  async function close(id: string) {
    await killTerminal(id).catch(() => undefined);
    live.current.get(id)?.terminal.dispose();
    live.current.delete(id);
    hosts.current.delete(id);
    onClosed(id);
  }

  if (!sessions.length) {
    return (
      <section className={`terminal-deck terminal-empty terminal-deck--${variant}`}>
        <span>{variant === "boot" ? "BOOT CONSOLE" : "SESSION DECK"}</span>
        <p>{variant === "boot" ? "Opening the supervised Ciel Runtime process…" : "Runtime and voice setup sessions will appear here when supervision is required."}</p>
      </section>
    );
  }

  return (
    <section className={`terminal-deck terminal-deck--${variant}`}>
      <nav className="terminal-tabs" aria-label="Terminal sessions">
        <span className="deck-label">{variant === "boot" ? "BOOT CONSOLE" : "SESSION DECK"}</span>
        {sessions.map((session) => (
          <button
            className={session.id === activeId ? "active" : ""}
            key={session.id}
            onClick={() => onActivate(session.id)}
          >
            <i data-kind={session.kind} />
            {session.title}
            <b
              role="button"
              aria-label={`Close ${session.title}`}
              onClick={(event) => {
                event.stopPropagation();
                void close(session.id);
              }}
            >
              ×
            </b>
          </button>
        ))}
      </nav>
      <div className="terminal-viewport" ref={viewport}>
        {sessions.map((session) => (
          <div
            className="terminal-host"
            key={session.id}
            hidden={session.id !== activeId}
            ref={(node) => {
              if (node) hosts.current.set(session.id, node);
              else hosts.current.delete(session.id);
            }}
          />
        ))}
      </div>
    </section>
  );
}
