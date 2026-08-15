import { useEffect, useRef, type FormEvent } from "react";
import { RichMessage } from "../components/RichMessage";
import type { ChannelMessage } from "../core/contracts";

export function CielChatApp({ messages, endpoint, draft, online, sending, notice, onDraft, onSubmit }: {
  messages: readonly ChannelMessage[];
  endpoint: string;
  draft: string;
  online: boolean;
  sending: boolean;
  notice: string;
  onDraft: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const transcript = useRef<HTMLDivElement | null>(null);
  useEffect(() => transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" }), [messages.length]);

  return (
    <section className="chat-window-app">
      <div className="chat-transcript" ref={transcript} aria-live="polite">
        {messages.length ? messages.slice(-50).map((message) => (
          <article key={message.id} data-user={message.sender_id === "cielarvis-user"}>
            <small>{message.sender_id === "cielarvis-user" ? "YOU" : message.sender_id || "CIEL"}</small>
            {message.sender_id === "cielarvis-user"
              ? <p>{message.message}</p>
              : <RichMessage message={message} endpoint={endpoint} />}
          </article>
        )) : <p className="empty-copy">The active Ciel agent is ready for a channel message.</p>}
      </div>
      <form className="chat-composer" onSubmit={onSubmit}>
        <div className="chat-notice"><i data-online={online} /><span>{notice}</span></div>
        <textarea value={draft} onChange={(event) => onDraft(event.target.value)} placeholder="Direct the active Ciel agent…" rows={2} />
        <button disabled={!online || sending || !draft.trim()}>{sending ? "SENDING" : "TRANSMIT"}</button>
      </form>
    </section>
  );
}
