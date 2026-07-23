import { describe, expect, it, vi } from "vitest";

vi.mock("../cards/GenericToolCard", () => ({ default: () => null }));

import { parseV1Props } from "./v1Adapter";

describe("v1Adapter spawn_subagent identity", () => {
  it("keeps the existing UI id and exposes call_id separately", () => {
    const parsed = parseV1Props({
      data: {
        id: "message-id",
        content: [
          {
            data: {
              id: "existing-ui-id",
              call_id: "runtime-call-id",
              name: "spawn_subagent",
              arguments: '{"task":"inspect"}',
            },
          },
        ],
      },
    });
    expect(parsed.content.id).toBe("existing-ui-id");
    expect(parsed.content.toolCallId).toBe("runtime-call-id");
  });

  it("does not promote legacy or generated ids to a backend binding id", () => {
    const parsed = parseV1Props({
      data: {
        id: "message-id",
        content: [
          {
            data: {
              id: "legacy-id",
              name: "spawn_subagent",
              arguments: {},
            },
          },
        ],
      },
    });
    expect(parsed.content.id).toBe("legacy-id");
    expect(parsed.content.toolCallId).toBeUndefined();
  });
});
