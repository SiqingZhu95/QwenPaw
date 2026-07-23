import { describe, expect, it } from "vitest";
import { SseFrameParser } from "./sseFrameParser";

describe("SseFrameParser", () => {
  it("parses frames split across arbitrary chunks", () => {
    const parser = new SseFrameParser();
    expect(parser.push("id: stream:1\r\nevent: sub")).toEqual([]);
    expect(parser.push('agent\r\ndata: {"sequence":1}\r\n\r\n')).toEqual([
      {
        id: "stream:1",
        event: "subagent",
        data: '{"sequence":1}',
      },
    ]);
  });

  it("joins multiline data and ignores heartbeat comments", () => {
    const parser = new SseFrameParser();
    expect(parser.push(": heartbeat\n\ndata: first\ndata: second\n\n")).toEqual(
      [{ id: undefined, event: undefined, data: "first\nsecond" }],
    );
  });

  it("flushes a final frame without a trailing blank line", () => {
    const parser = new SseFrameParser();
    parser.push("event: subagent\ndata: final");
    expect(parser.finish()).toEqual([
      { id: undefined, event: "subagent", data: "final" },
    ]);
  });
});
