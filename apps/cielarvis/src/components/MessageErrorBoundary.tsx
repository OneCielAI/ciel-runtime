import { Component, type ReactNode } from "react";

type Props = { children: ReactNode; fallback: string };
type State = { failed: boolean };

export class MessageErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("CIELARVIS message preview failed", error);
  }

  render() {
    if (this.state.failed) {
      return <pre className="message-preview-fallback">{this.props.fallback}</pre>;
    }
    return this.props.children;
  }
}
