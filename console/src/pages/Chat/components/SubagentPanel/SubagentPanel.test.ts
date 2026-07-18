import { describe, expect, it } from "vitest";
import { extractMessageText } from "./messageUtils";

describe("SubagentPanel message conversion", () => {
  it("joins text blocks for markdown rendering", () => {
    expect(
      extractMessageText([
        { type: "text", text: "First" },
        { type: "text", text: "Second" },
      ]),
    ).toBe("First\n\nSecond");
  });

  it("preserves tool blocks as readable json", () => {
    const text = extractMessageText([
      { type: "tool_use", name: "read_file", arguments: { path: "a.md" } },
    ]);
    expect(text).toContain("```json");
    expect(text).toContain("read_file");
  });
});
