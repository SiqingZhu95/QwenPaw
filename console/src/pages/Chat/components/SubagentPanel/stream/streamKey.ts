import type { SubagentStreamOwner } from "./types";

export function createSubagentStreamKey(
  owner: SubagentStreamOwner,
  parentToolCallId: string,
): string {
  return JSON.stringify([
    owner.agentId,
    owner.parentSessionId,
    parentToolCallId,
  ]);
}
