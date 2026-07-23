export interface SubagentStreamOwner {
  agentId: string;
  parentSessionId: string;
  parentUserId: string;
  parentChannel: string;
}

export interface SubagentStreamSnapshot {
  stream_id: string;
  status:
    | "preparing"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "expired";
  fork: boolean;
  background: boolean;
  child_session_id?: string | null;
  child_chat_id?: string | null;
  task_id?: string | null;
  latest_sequence: number;
  first_sequence: number;
}

export interface ResolveSubagentStreamResponse {
  found: boolean;
  retry_after_ms?: number | null;
  stream?: SubagentStreamSnapshot | null;
}

export interface SubagentStreamEnvelope {
  stream_id: string;
  sequence: number;
  kind: "metadata" | "runtime" | "status" | "error";
  payload: Record<string, unknown>;
  created_at?: number;
}

export interface RuntimeResponseViewModel {
  id: string;
  object?: string;
  status: string;
  created_at: number;
  output: Array<Record<string, unknown>>;
  error?: Record<string, unknown>;
}

export type SubagentStreamConnectionStatus =
  | "idle"
  | "resolving"
  | "connecting"
  | "streaming"
  | "reconnecting"
  | "completed"
  | "failed"
  | "fallback";

export interface SubagentStreamRecord {
  key: string;
  tabId: string;
  owner: Readonly<SubagentStreamOwner>;
  parentToolCallId: string;
  streamId?: string;
  status: SubagentStreamConnectionStatus;
  lastSequence: number;
  snapshot?: SubagentStreamSnapshot;
  viewModel?: RuntimeResponseViewModel;
  errorCode?: string;
}
