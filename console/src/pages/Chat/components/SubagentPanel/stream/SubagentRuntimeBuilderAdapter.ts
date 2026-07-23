// Keep this SDK deep import isolated. A vendor path change must fail the build.
import AgentScopeRuntimeResponseBuilder from "@agentscope-ai/chat/lib/AgentScopeRuntimeWebUI/core/AgentScopeRuntime/Response/Builder";
import type { RuntimeResponseViewModel } from "./types";

export class SubagentRuntimeBuilderAdapter {
  private readonly builder: AgentScopeRuntimeResponseBuilder;

  constructor(streamId: string) {
    this.builder = new AgentScopeRuntimeResponseBuilder({
      id: streamId,
      status: "created" as never,
      created_at: Date.now(),
    });
  }

  handle(payload: Record<string, unknown>): RuntimeResponseViewModel {
    return this.builder.handle(
      payload as never,
    ) as unknown as RuntimeResponseViewModel;
  }

  setStatus(status: string): RuntimeResponseViewModel {
    return this.builder.handle({
      id: this.builder.data.id,
      object: "response",
      status,
      created_at: this.builder.data.created_at,
      output: [],
    } as never) as unknown as RuntimeResponseViewModel;
  }

  static mergeToolMessages(
    messages: Array<Record<string, unknown>>,
  ): Array<Record<string, unknown>> {
    return AgentScopeRuntimeResponseBuilder.mergeToolMessages(
      messages as never,
    ) as unknown as Array<Record<string, unknown>>;
  }
}
