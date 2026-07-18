import { beforeEach, describe, expect, it } from "vitest";
import { extractSubagentResultRefs } from "./subagentMetadata";
import { useSubagentPanelStore } from "../../../../stores/subagentPanelStore";

describe("SpawnSubagentCard helpers", () => {
  beforeEach(() => {
    useSubagentPanelStore.getState().reset();
  });

  it("extracts foreground session metadata", () => {
    expect(
      extractSubagentResultRefs("[SESSION: sub-12345678]\n\nSubagent response"),
    ).toEqual({ sessionId: "sub-12345678", taskId: undefined });
  });

  it("extracts background task and session metadata", () => {
    expect(
      extractSubagentResultRefs(
        "[TASK_ID: task-42]\n[SESSION: sub-abcdef12]\n\nSubmitted",
      ),
    ).toEqual({ sessionId: "sub-abcdef12", taskId: "task-42" });
  });

  it("updates an explicitly opened placeholder tab when metadata arrives", () => {
    const store = useSubagentPanelStore.getState();
    store.openTab({
      id: "call-1",
      task: "inspect repository",
      background: false,
      status: "running",
    });

    useSubagentPanelStore.getState().syncTab({
      id: "call-1",
      sessionId: "sub-12345678",
      status: "completed",
    });

    expect(useSubagentPanelStore.getState().tabs).toEqual([
      expect.objectContaining({
        id: "call-1",
        sessionId: "sub-12345678",
        status: "completed",
      }),
    ]);
  });

  it("does not create a tab from passive tool-card updates", () => {
    useSubagentPanelStore.getState().syncTab({
      id: "not-opened",
      sessionId: "sub-unused",
      status: "completed",
    });
    expect(useSubagentPanelStore.getState().tabs).toHaveLength(0);
  });
});
