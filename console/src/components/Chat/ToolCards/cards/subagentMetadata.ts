import { stringifyResult } from "../shared/utils";

export interface SubagentResultRefs {
  sessionId?: string;
  taskId?: string;
}

export function extractSubagentResultRefs(result: unknown): SubagentResultRefs {
  const text = stringifyResult(result);
  const sessionMatch = text.match(/\[SESSION:\s*([^\]\r\n]+)\]/i);
  const taskMatch = text.match(/\[TASK_ID:\s*([^\]\r\n]+)\]/i);
  return {
    sessionId: sessionMatch?.[1]?.trim(),
    taskId: taskMatch?.[1]?.trim(),
  };
}
