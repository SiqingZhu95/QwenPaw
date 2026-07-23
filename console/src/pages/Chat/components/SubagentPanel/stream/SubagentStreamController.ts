import { SubagentRuntimeBuilderAdapter } from "./SubagentRuntimeBuilderAdapter";
import {
  subagentStreamApi,
  SubagentStreamApiClient,
} from "./subagentStreamApi";
import { useSubagentStreamStore } from "./subagentStreamStore";
import type { SubagentStreamEnvelope, SubagentStreamOwner } from "./types";

const TERMINAL = new Set(["completed", "failed", "cancelled", "expired"]);
const BACKOFF_MS = [500, 1000, 2000, 5000];

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export interface SubagentStreamControllerOptions {
  key: string;
  tabId: string;
  parentToolCallId: string;
  owner: SubagentStreamOwner;
  waitForBinding?: boolean;
  api?: SubagentStreamApiClient;
}

export class SubagentStreamController {
  readonly key: string;
  readonly owner: Readonly<SubagentStreamOwner>;
  private readonly parentToolCallId: string;
  private readonly waitForBinding: boolean;
  private readonly api: SubagentStreamApiClient;
  private abortController?: AbortController;
  private builder?: SubagentRuntimeBuilderAdapter;
  private resolvePromise?: Promise<string | undefined>;
  private connectionPromise?: Promise<void>;
  private active = false;
  private disposed = false;

  constructor(options: SubagentStreamControllerOptions) {
    this.key = options.key;
    this.owner = Object.freeze({ ...options.owner });
    this.parentToolCallId = options.parentToolCallId;
    this.waitForBinding = options.waitForBinding !== false;
    this.api = options.api || subagentStreamApi;
    useSubagentStreamStore.getState().ensure({
      key: options.key,
      tabId: options.tabId,
      owner: this.owner,
      parentToolCallId: options.parentToolCallId,
      status: "idle",
      lastSequence: 0,
    });
  }

  prefetch(): Promise<string | undefined> {
    if (this.disposed) return Promise.resolve(undefined);
    const current = useSubagentStreamStore.getState().records[this.key];
    if (current?.streamId) return Promise.resolve(current.streamId);
    if (!this.resolvePromise) {
      this.resolvePromise = this.resolveStream().finally(() => {
        this.resolvePromise = undefined;
      });
    }
    return this.resolvePromise;
  }

  private async resolveStream(): Promise<string | undefined> {
    useSubagentStreamStore.getState().patch(this.key, { status: "resolving" });
    const maxAttempts = this.waitForBinding ? 6 : 1;
    const waitTimeoutMs = this.waitForBinding ? 1000 : 0;
    for (
      let attempt = 0;
      attempt < maxAttempts && !this.disposed;
      attempt += 1
    ) {
      try {
        const result = await this.api.resolve(
          this.owner,
          this.parentToolCallId,
          waitTimeoutMs,
        );
        if (result.found && result.stream) {
          this.builder = new SubagentRuntimeBuilderAdapter(
            result.stream.stream_id,
          );
          useSubagentStreamStore.getState().patch(this.key, {
            streamId: result.stream.stream_id,
            snapshot: result.stream,
            status: "connecting",
          });
          return result.stream.stream_id;
        }
        if (!this.waitForBinding) break;
        await delay(result.retry_after_ms || 500);
      } catch {
        if (attempt === maxAttempts - 1) break;
        await delay(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
      }
    }
    useSubagentStreamStore.getState().patch(this.key, {
      status: "fallback",
      errorCode: "stream_not_found",
    });
    return undefined;
  }

  activate(): void {
    if (this.disposed) return;
    this.active = true;
    if (!this.connectionPromise) {
      this.connectionPromise = this.connectLoop().finally(() => {
        this.connectionPromise = undefined;
      });
    }
  }

  deactivate(): void {
    this.active = false;
    this.abortController?.abort();
    this.abortController = undefined;
  }

  dispose(): void {
    this.disposed = true;
    this.deactivate();
  }

  private async connectLoop(): Promise<void> {
    const streamId = await this.prefetch();
    if (!streamId || !this.active || this.disposed) return;
    let failures = 0;
    while (this.active && !this.disposed) {
      const record = useSubagentStreamStore.getState().records[this.key];
      if (!record || TERMINAL.has(record.snapshot?.status || "")) return;
      this.abortController = new AbortController();
      useSubagentStreamStore.getState().patch(this.key, {
        status: failures ? "reconnecting" : "connecting",
      });
      try {
        for await (const envelope of this.api.connectEvents(
          this.owner,
          streamId,
          record.lastSequence,
          this.abortController.signal,
        )) {
          if (!this.active || this.disposed) return;
          try {
            this.handleEnvelope(envelope);
          } catch {
            useSubagentStreamStore.getState().patch(this.key, {
              status: "fallback",
              errorCode: "stream_render_failed",
            });
            this.deactivate();
            return;
          }
        }
        failures += 1;
      } catch (error) {
        if (isAbort(error) || !this.active || this.disposed) return;
        failures += 1;
      }
      if (!this.active || this.disposed) return;
      const current = useSubagentStreamStore.getState().records[this.key];
      if (current?.status === "completed" || current?.status === "failed")
        return;
      const base = BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)];
      await delay(base + Math.floor(Math.random() * 150));
    }
  }

  private handleEnvelope(envelope: SubagentStreamEnvelope): void {
    const store = useSubagentStreamStore.getState();
    const current = store.records[this.key];
    if (!current || envelope.stream_id !== current.streamId) return;
    const resetRequired = envelope.payload.reset_required === true;
    if (resetRequired) {
      store.patch(this.key, {
        status: "fallback",
        errorCode: "stream_gap",
      });
      this.deactivate();
      return;
    }
    if (envelope.kind === "metadata" && envelope.payload.connected === true) {
      store.patch(this.key, { status: "streaming" });
    }
    if (envelope.sequence > 0 && envelope.sequence <= current.lastSequence)
      return;
    if (
      envelope.sequence > 0 &&
      current.lastSequence > 0 &&
      envelope.sequence > current.lastSequence + 1
    ) {
      store.patch(this.key, { status: "fallback", errorCode: "stream_gap" });
      this.deactivate();
      return;
    }

    const sequencePatch =
      envelope.sequence > current.lastSequence
        ? { lastSequence: envelope.sequence }
        : {};
    if (envelope.kind === "runtime") {
      this.builder ||= new SubagentRuntimeBuilderAdapter(envelope.stream_id);
      store.patch(this.key, {
        ...sequencePatch,
        status: "streaming",
        viewModel: this.builder.handle(envelope.payload),
      });
      return;
    }
    if (envelope.kind === "status" || envelope.kind === "error") {
      const runtimeStatus = String(
        envelope.payload.status ||
          (envelope.kind === "error" ? "failed" : "running"),
      );
      this.builder ||= new SubagentRuntimeBuilderAdapter(envelope.stream_id);
      const terminalFailure = ["failed", "cancelled", "expired"].includes(
        runtimeStatus,
      );
      store.patch(this.key, {
        ...sequencePatch,
        status:
          runtimeStatus === "completed"
            ? "completed"
            : terminalFailure
            ? "failed"
            : "streaming",
        errorCode:
          typeof envelope.payload.code === "string"
            ? envelope.payload.code
            : undefined,
        viewModel: this.builder.setStatus(runtimeStatus),
      });
      return;
    }
    store.patch(this.key, { ...sequencePatch, status: "streaming" });
  }
}
