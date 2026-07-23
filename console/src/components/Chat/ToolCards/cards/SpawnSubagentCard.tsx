import React, { useCallback, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ApartmentOutlined } from "@ant-design/icons";
import type { ToolCallContent } from "../shared/types";
import { ToolCardShell } from "../shared";
import {
  type SubagentExecutionStatus,
  useSubagentPanelStore,
} from "../../../../stores/subagentPanelStore";
import { stringifyResult } from "../shared/utils";
import { extractSubagentResultRefs } from "./subagentMetadata";
import { useAgentStore } from "../../../../stores/agentStore";
import sessionApi from "../../../../pages/Chat/sessionApi";
import { createSubagentStreamKey } from "../../../../pages/Chat/components/SubagentPanel/stream/streamKey";
import { subagentStreamControllerRegistry } from "../../../../pages/Chat/components/SubagentPanel/stream/SubagentStreamControllerRegistry";

export interface SpawnSubagentCardProps {
  content: ToolCallContent;
  isStreaming?: boolean;
}

function toolExecutionStatus(
  content: ToolCallContent,
  hasBackgroundTask: boolean,
): SubagentExecutionStatus {
  if (content.status === "error") return "failed";
  // A completed background tool call only means the task was submitted. The
  // side panel polls task status before changing this to completed.
  if (hasBackgroundTask) return "running";
  return content.status === "done" ? "completed" : "running";
}

const SpawnSubagentCard: React.FC<SpawnSubagentCardProps> = ({
  content,
  isStreaming,
}) => {
  const { t } = useTranslation();
  const agentId = useAgentStore((state) => state.selectedAgent);
  const params = content.params || {};
  const task = String(params.task || "");
  const background = params.background === true;
  const taskShort = task.length > 36 ? `${task.slice(0, 36)}…` : task;
  const refs = useMemo(
    () => extractSubagentResultRefs(content.result),
    [content.result],
  );
  const status = toolExecutionStatus(content, !!refs.taskId);
  const streamRegistration = useMemo(() => {
    if (!content.toolCallId) return undefined;
    const identity = sessionApi.getSessionIdentity();
    if (!agentId || !identity.sessionId) return undefined;
    const owner = {
      agentId,
      parentSessionId: identity.sessionId,
      parentUserId: identity.userId,
      parentChannel: identity.channel,
    };
    return {
      key: createSubagentStreamKey(owner, content.toolCallId),
      tabId: content.id,
      parentToolCallId: content.toolCallId,
      owner,
      waitForBinding: content.status === "calling",
    };
  }, [agentId, content.id, content.status, content.toolCallId]);

  const tab = useMemo(
    () => ({
      id: content.id,
      streamKey: streamRegistration?.key,
      parentSessionId: streamRegistration?.owner.parentSessionId,
      agentId,
      sessionId: refs.sessionId,
      taskId: refs.taskId,
      task,
      background,
      status,
      error:
        content.status === "error"
          ? stringifyResult(content.result) || t("subagentPanel.failed")
          : undefined,
    }),
    [
      agentId,
      background,
      content.id,
      content.result,
      content.status,
      refs.sessionId,
      refs.taskId,
      status,
      streamRegistration?.owner.parentSessionId,
      streamRegistration?.key,
      t,
      task,
    ],
  );

  useEffect(() => {
    useSubagentPanelStore.getState().syncTab(tab);
  }, [tab]);

  useEffect(() => {
    // Resolve eagerly only for the live tool call. Historical cards stay
    // network-silent until the user explicitly opens their tab.
    if (streamRegistration && content.status === "calling") {
      subagentStreamControllerRegistry.prefetch(streamRegistration);
    }
  }, [content.status, streamRegistration]);

  const openPanel = useCallback(() => {
    if (streamRegistration) {
      subagentStreamControllerRegistry.prefetch(streamRegistration);
    }
    useSubagentPanelStore.getState().openTab(tab);
  }, [streamRegistration, tab]);

  return (
    <ToolCardShell
      content={content}
      isStreaming={isStreaming}
      icon={<ApartmentOutlined />}
      title={
        taskShort
          ? t("subagentPanel.spawnTitle", { task: taskShort })
          : t("subagentPanel.spawnTitleDefault")
      }
      inlineResult={
        refs.sessionId
          ? t("subagentPanel.sessionLabel", { id: refs.sessionId })
          : null
      }
      onTitleClick={openPanel}
    />
  );
};

export default SpawnSubagentCard;
