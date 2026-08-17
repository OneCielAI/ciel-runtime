import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: string };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: "" };

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("CIELARVIS renderer failure", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="renderer-recovery" role="alert">
        <span className="brand-mark">C</span>
        <h1>CIELARVIS display recovery</h1>
        <p>The desktop renderer encountered an error. Runtime and terminal processes were not stopped.</p>
        <code>{this.state.error}</code>
        <button onClick={() => window.location.reload()}>RELOAD DESKTOP</button>
      </main>
    );
  }
}
