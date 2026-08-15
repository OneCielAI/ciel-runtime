import type { RuntimeSnapshot } from "../core/contracts";

export function AgentStage({ snapshot, listening }: { snapshot: RuntimeSnapshot | null; listening: boolean }) {
  const state = !snapshot?.connected ? "offline" : listening ? "thinking" : "ready";
  return (
    <section className="agent-stage" data-state={state}>
      <div className="radar-grid" aria-hidden="true" />
      <div className="agent-orbit orbit-outer" />
      <div className="agent-orbit orbit-inner" />
      <div className="agent-core">
        <div className="core-glint" />
        <strong>CIEL</strong>
        <span>{state}</span>
      </div>
      <div className="stage-caption">
        <span>AGENT PRESENCE</span>
        <h1>{snapshot?.connected ? "Runtime synchronized" : "Awaiting runtime link"}</h1>
        <p>
          {snapshot?.connected
            ? "The visual layer is bound to the active Ciel channel."
            : "Cielavis is opening a supervised Runtime session above."}
        </p>
      </div>
    </section>
  );
}
