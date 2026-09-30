import { Component, type ReactNode } from "react";

interface Props {
  fallback: ReactNode; // what shows instead, once something inside has thrown
  children: ReactNode;
}

/**
 * What shows when drawing throws, instead of a blank page: a bug, or the editor's code failing
 * to load after a deploy. React logs the error itself. A class, as React still requires for this.
 */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
