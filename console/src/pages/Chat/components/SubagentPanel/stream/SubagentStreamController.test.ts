import { afterEach, describe, expect, it, vi } from "vitest";
import type { SubagentStreamApiClient } from "./subagentStreamApi";
import { SubagentStreamController } from "./SubagentStreamController";
import { useSubagentStreamStore } from "./subagentStreamStore";
import type { SubagentStreamEnvelope, SubagentStreamOwner } from "./types";

vi.mock("./SubagentRuntimeBuilderAdapter", () => ({
  SubagentRuntimeBuilderAdapter: class {
    private readonly streamId: string;

    constructor(streamId: string) {
      this.streamId = streamId;
    }

    handle(payload: Record<string, unknown>) {
      return this.view(String(payload.status || "running"));
    }

    setStatus(status: string) {
      return this.view(status);
    }

    private view(status: string) {
      return {
        id: this.streamId,
        status,
        created_at: 1,
        output: [],
      };
    }
  },
}));

const owner: SubagentStreamOwner = {
  agentId: "default",
  parentSessionId: "parent",
  parentUserId: "user",
  parentChannel: "console",
};

function apiFor(events: SubagentStreamEnvelope[]): SubagentStreamApiClient {
  return {
    resolve: vi.fn(async () => ({
      found: true,
      stream: {
        stream_id: "stream-1",
        status: "running",
        fork: false,
        background: false,
        latest_sequence: 0,
        first_sequence: 0,
      },
    })),
    getMetadata: vi.fn(),
    connectEvents: vi.fn(async function* () {
      for (const event of events) yield event;
    }),
  } as unknown as SubagentStreamApiClient;
}

function envelope(
  sequence: number,
  kind: SubagentStreamEnvelope["kind"],
  payload: Record<string, unknown>,
): SubagentStreamEnvelope {
  return { stream_id: "stream-1", sequence, kind, payload };
}

afterEach(() => {
  useSubagentStreamStore.getState().reset();
});

describe("SubagentStreamController", () => {
  it("binds a terminal event to its frozen owner and stream record", async () => {
    const controller = new SubagentStreamController({
      key: "key-1",
      tabId: "tab-1",
      parentToolCallId: "call-1",
      owner,
      api: apiFor([
        envelope(0, "metadata", { connected: true }),
        envelope(1, "status", { status: "completed" }),
      ]),
    });

    controller.activate();
    await vi.waitFor(() => {
      expect(useSubagentStreamStore.getState().records["key-1"].status).toBe(
        "completed",
      );
    });

    expect(controller.owner).toEqual(owner);
    expect(Object.isFrozen(controller.owner)).toBe(true);
    expect(
      useSubagentStreamStore.getState().records["key-1"].lastSequence,
    ).toBe(1);
    controller.dispose();
  });

  it("falls back when replay contains a sequence gap", async () => {
    const controller = new SubagentStreamController({
      key: "key-gap",
      tabId: "tab-gap",
      parentToolCallId: "call-gap",
      owner,
      api: apiFor([
        envelope(1, "status", { status: "running" }),
        envelope(3, "status", { status: "running" }),
      ]),
    });

    controller.activate();
    await vi.waitFor(() => {
      expect(
        useSubagentStreamStore.getState().records["key-gap"].errorCode,
      ).toBe("stream_gap");
    });

    expect(useSubagentStreamStore.getState().records["key-gap"].status).toBe(
      "fallback",
    );
    controller.dispose();
  });
});
