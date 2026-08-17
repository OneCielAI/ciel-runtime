import { useEffect, useRef, type FormEvent } from "react";
import { RichMessage } from "../components/RichMessage";
import { MessageErrorBoundary } from "../components/MessageErrorBoundary";
import type { ChannelMessage } from "../core/contracts";
import { capabilityMessagePresentation } from "../core/agentCapabilities";

export function CielChatApp({ messages, endpoint, draft, online, sending, notice, onDraft, onSubmit, voiceReady, voiceActive, voiceBusy, onVoice }: {
  messages: readonly ChannelMessage[];
  endpoint: string;
  draft: string;
  online: boolean;
  sending: boolean;
  notice: string;
  onDraft: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  voiceReady: boolean;
  voiceActive: boolean;
  voiceBusy: boolean;
  onVoice: () => void;
}) {
  const transcript = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = transcript.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length]);

  return (
    <section className="chat-window-app">
      <div className="chat-transcript" ref={transcript} aria-live="polite">
        {messages.length ? messages.slice(-50).map((message) => {
          const presentation = capabilityMessagePresentation(message);
          const user = message.sender_id === "cielarvis-user";
          return (
            <article key={message.id} data-user={user && !presentation.internal} data-internal={presentation.internal}>
              <small>{presentation.internal ? "CIELARVIS" : user ? "YOU" : message.sender_id || "CIEL"}</small>
              {user || presentation.internal
                ? <p>{presentation.text}</p>
                : (
                  <MessageErrorBoundary fallback={String(message.message ?? "")}>
                    <RichMessage message={message} endpoint={endpoint} />
                  </MessageErrorBoundary>
                )}
            </article>
          );
        }) : <p className="empty-copy">The active Ciel agent is ready for a channel message.</p>}
      </div>
      <form className="chat-composer" onSubmit={onSubmit}>
        <div className="chat-notice"><i data-online={online} /><span>{notice}</span></div>
        <div className="chat-input-row">
          <button
            type="button"
            className="chat-microphone"
            data-ready={voiceReady}
            data-active={voiceActive}
            disabled={voiceBusy}
            title={voiceReady ? (voiceActive ? "Stop and send voice input" : "Open microphone") : "Ask the active agent to install or recover voice input"}
            onClick={onVoice}
          >
            {voiceBusy ? "…" : voiceActive ? "■" : "●"}
          </button>
          <textarea value={draft} onChange={(event) => onDraft(event.target.value)} placeholder="Direct the active Ciel agent…" rows={2} />
        </div>
        <button disabled={!online || sending || !draft.trim()}>{sending ? "SENDING" : "TRANSMIT"}</button>
      </form>
    </section>
  );
}
