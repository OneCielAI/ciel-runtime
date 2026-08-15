import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import type { ChannelMessage } from "../core/contracts";

type Attachment = {
  content_type?: string;
  name?: string;
  original_name?: string;
  path?: string;
  url?: string;
};

function responseText(message: ChannelMessage): string {
  const meta = message.meta ?? {};
  const candidate = meta.web_response ?? meta.response;
  if (candidate && typeof candidate === "object") {
    const response = candidate as Record<string, unknown>;
    const overview = typeof response.overview === "string" ? response.overview.trim() : "";
    const details = typeof response.details === "string" ? response.details.trim() : "";
    if (overview && details) return `${overview}\n\n${details}`;
    if (overview || details) return overview || details;
    if (typeof response.spoken === "string") return response.spoken;
  }
  return String(message.message ?? "");
}

function attachments(message: ChannelMessage): Attachment[] {
  const values = message.meta?.attachments;
  return Array.isArray(values)
    ? values.filter((value): value is Attachment => Boolean(value && typeof value === "object"))
    : [];
}

function attachmentUrl(attachment: Attachment, endpoint: string): string {
  const value = String(attachment.path || attachment.url || "").trim();
  if (!value) return "";
  try {
    return new URL(value, `${endpoint.replace(/\/$/, "")}/`).toString();
  } catch {
    return "";
  }
}

function isImage(attachment: Attachment): boolean {
  const type = String(attachment.content_type || "").toLowerCase();
  const name = String(attachment.original_name || attachment.name || "").toLowerCase();
  return type.startsWith("image/") || /\.(avif|bmp|gif|jpe?g|png|svg|webp)$/.test(name);
}

function isHtml(attachment: Attachment): boolean {
  const type = String(attachment.content_type || "").toLowerCase();
  const name = String(attachment.original_name || attachment.name || "").toLowerCase();
  return type === "text/html" || type === "application/xhtml+xml" || /\.html?$/.test(name);
}

export function RichMessage({ message, endpoint }: { message: ChannelMessage; endpoint: string }) {
  const files = attachments(message);
  return (
    <div className="rich-message">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={{
          a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener">{children}</a>,
          img: ({ alt, ...props }) => <img {...props} alt={alt || "AI response image"} loading="lazy" referrerPolicy="no-referrer" />,
        }}
      >
        {responseText(message)}
      </ReactMarkdown>
      {files.length > 0 && (
        <div className="attachment-gallery">
          {files.map((file, index) => {
            const url = attachmentUrl(file, endpoint);
            const label = file.original_name || file.name || `Attachment ${index + 1}`;
            if (!url) return null;
            if (isImage(file)) {
              return <figure key={`${url}-${index}`}><img src={url} alt={label} loading="lazy" referrerPolicy="no-referrer" /><figcaption>{label}</figcaption></figure>;
            }
            if (isHtml(file)) {
              return <figure className="html-attachment" key={`${url}-${index}`}><iframe src={url} title={label} loading="lazy" sandbox="" referrerPolicy="no-referrer" /><figcaption><a href={url} target="_blank" rel="noreferrer noopener">Open {label}</a></figcaption></figure>;
            }
            return <a className="attachment-link" key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer noopener">Download {label}</a>;
          })}
        </div>
      )}
    </div>
  );
}

export const richMessageText = responseText;
