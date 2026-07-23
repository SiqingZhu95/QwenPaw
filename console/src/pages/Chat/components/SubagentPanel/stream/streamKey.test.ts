import { describe, expect, it } from "vitest";
import { createSubagentStreamKey } from "./streamKey";

const owner = {
  agentId: "default",
  parentSessionId: "session-a",
  parentUserId: "user",
  parentChannel: "console",
};

describe("createSubagentStreamKey", () => {
  it("separates agent, parent session and parent tool call", () => {
    const base = createSubagentStreamKey(owner, "call-a");
    expect(createSubagentStreamKey(owner, "call-b")).not.toBe(base);
    expect(
      createSubagentStreamKey(
        { ...owner, parentSessionId: "session-b" },
        "call-a",
      ),
    ).not.toBe(base);
    expect(
      createSubagentStreamKey({ ...owner, agentId: "other" }, "call-a"),
    ).not.toBe(base);
  });
});
