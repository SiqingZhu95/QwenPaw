import { buildAuthHeaders } from "../../../../../api/authHeaders";
import { getApiUrl } from "../../../../../api/config";
import { request } from "../../../../../api/request";
import { SseFrameParser } from "./sseFrameParser";
import type {
  ResolveSubagentStreamResponse,
  SubagentStreamEnvelope,
  SubagentStreamOwner,
  SubagentStreamSnapshot,
} from "./types";

function ownerBody(owner: SubagentStreamOwner) {
  return {
    agent_id: owner.agentId,
    parent_session_id: owner.parentSessionId,
    parent_user_id: owner.parentUserId,
    parent_channel: owner.parentChannel,
  };
}

function ownerHeaders(owner: SubagentStreamOwner): Record<string, string> {
  return {
    ...buildAuthHeaders(),
    "Content-Type": "application/json",
    "X-Agent-Id": owner.agentId,
  };
}

export class SubagentStreamApiClient {
  resolve(
    owner: SubagentStreamOwner,
    parentToolCallId: string,
  ): Promise<ResolveSubagentStreamResponse> {
    return request<ResolveSubagentStreamResponse>("/subagent-streams/resolve", {
      method: "POST",
      headers: ownerHeaders(owner),
      body: JSON.stringify({
        ...ownerBody(owner),
        parent_tool_call_id: parentToolCallId,
      }),
    });
  }

  getMetadata(
    owner: SubagentStreamOwner,
    streamId: string,
  ): Promise<SubagentStreamSnapshot> {
    return request<SubagentStreamSnapshot>(
      `/subagent-streams/${encodeURIComponent(streamId)}/metadata`,
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify(ownerBody(owner)),
      },
    );
  }

  async *connectEvents(
    owner: SubagentStreamOwner,
    streamId: string,
    afterSequence: number,
    signal: AbortSignal,
  ): AsyncGenerator<SubagentStreamEnvelope, void, void> {
    const response = await fetch(
      getApiUrl(`/subagent-streams/${encodeURIComponent(streamId)}/events`),
      {
        method: "POST",
        headers: ownerHeaders(owner),
        body: JSON.stringify({
          ...ownerBody(owner),
          after_sequence: afterSequence,
        }),
        signal,
      },
    );
    if (!response.ok || !response.body) {
      throw new Error(`subagent_stream_connect_${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseFrameParser();
    const parseFrames = (frames: ReturnType<SseFrameParser["push"]>) =>
      frames.map((frame) => JSON.parse(frame.data) as SubagentStreamEnvelope);

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const envelope of parseFrames(
        parser.push(decoder.decode(value, { stream: true })),
      )) {
        yield envelope;
      }
    }
    const tail = decoder.decode();
    for (const envelope of parseFrames([
      ...parser.push(tail),
      ...parser.finish(),
    ])) {
      yield envelope;
    }
  }
}

export const subagentStreamApi = new SubagentStreamApiClient();
