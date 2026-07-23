import { Alert } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  resetKey: string;
}

interface State {
  hasError: boolean;
}

/** Keeps malformed child-stream output from breaking the parent chat page. */
export class SubagentStreamErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Failed to render subagent stream output", error, info);
  }

  componentDidUpdate(previous: Props): void {
    if (this.state.hasError && previous.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <Alert
          type="warning"
          showIcon
          message="Unable to render this subagent stream message."
        />
      );
    }
    return this.props.children;
  }
}
