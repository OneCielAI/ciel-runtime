import { useEffect, useRef, useState } from "react";
import { CIEL_JS_APP_API, type CielAppManifest, type CielJsAppMessage, type CielJsHostMessage } from "../core/desktopSdk";

export function JavascriptAppHost({ app }: { app: CielAppManifest }) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  if (app.host.kind !== "javascript") throw new Error(`App ${app.id} is not a JavaScript package`);

  useEffect(() => {
    const receive = (event: MessageEvent<CielJsAppMessage>) => {
      if (event.source !== frame.current?.contentWindow || !event.data || typeof event.data !== "object") return;
      if (event.data.type === "cielarvis.app.ready" && event.data.api === CIEL_JS_APP_API) setState("ready");
      if (event.data.type === "cielarvis.app.error") setState("error");
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);

  const initialize = () => {
    const message: CielJsHostMessage = {
      type: "cielarvis.host.initialize",
      api: CIEL_JS_APP_API,
      appId: app.id,
      capabilities: app.capabilities,
    };
    frame.current?.contentWindow?.postMessage(message, "*");
  };

  return (
    <section className="javascript-app-host" data-state={state}>
      <iframe ref={frame} src={app.host.entrypoint} title={app.name} sandbox="allow-scripts" referrerPolicy="no-referrer" onLoad={initialize} />
      {state !== "ready" && <small>{state === "error" ? "The application reported an error." : `Starting ${app.name} in the JS sandbox…`}</small>}
    </section>
  );
}
