import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, Spin, Tabs, Tag, Tooltip } from "antd";
import {
  CloseOutlined,
  ReloadOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { Markdown } from "@agentscope-ai/chat";
import { useTranslation } from "react-i18next";
import { agentApi } from "../../../../api/modules/agent";
import { chatApi } from "../../../../api/modules/chat";
import type { ChatHistory, Message } from "../../../../api/types/chat";
import {
  type SubagentExecutionStatus,
  type SubagentTab,
  useSubagentPanelStore,
} from "../../../../stores/subagentPanelStore";
import styles from "./index.module.less";
import { extractMessageText } from "./messageUtils";
import { SubagentStreamView } from "./stream/SubagentStreamView";
import { subagentStreamControllerRegistry } from "./stream/SubagentStreamControllerRegistry";
import { useSubagentStreamStore } from "./stream/subagentStreamStore";
import sessionApi from "../../sessionApi";

const POLL_INTERVAL_MS = 1500;
const TERMINAL_SUCCESS = new Set(["completed", "succeeded", "finished"]);
const TERMINAL_FAILURE = new Set(["failed", "error", "cancelled", "canceled"]);

function errorText(value: unknown): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function statusColor(status: SubagentExecutionStatus): string {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  return "processing";
}

function MessageHistory({ messages }: { messages: Message[] }) {
  const { t } = useTranslation();
  if (messages.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("subagentPanel.noMessages")}
      />
    );
  }

  return (
    <div className={styles.messageList}>
      {messages.map((message, index) => {
        const role = String(message.role || "assistant");
        const text = extractMessageText(message.content);
        return (
          <div
            key={String(message.id || `${role}-${index}`)}
            className={`${styles.messageRow} ${
              role === "user" ? styles.messageRowUser : styles.messageRowAgent
            }`}
          >
            <div className={styles.messageRole}>
              {role === "user"
                ? t("subagentPanel.user")
                : t("subagentPanel.subagent")}
            </div>
            <div className={styles.messageBubble}>
              {text ? (
                <Markdown content={text} baseFontSize={13} />
              ) : (
                <pre>{JSON.stringify(message.content, null, 2)}</pre>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LegacySubagentSessionView({ tab }: { tab: SubagentTab }) {
  const { t } = useTranslation();
  const [history, setHistory] = useState<ChatHistory | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [taskPhase, setTaskPhase] = useState("");

  const syncTab = useSubagentPanelStore((state) => state.syncTab);

  const loadHistory = useCallback(async () => {
    if (!tab.sessionId) return false;
    setLoadingHistory(true);
    try {
      const chats = await chatApi.listChats(undefined, tab.agentId);
      const chat = chats.find((item) => item.session_id === tab.sessionId);
      if (!chat) {
        setLoadError(t("subagentPanel.sessionPending"));
        return false;
      }
      const nextHistory = await chatApi.getChat(chat.id, tab.agentId);
      setHistory(nextHistory);
      setLoadError("");
      return true;
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setLoadingHistory(false);
    }
  }, [t, tab.agentId, tab.sessionId]);

  useEffect(() => {
    if (!tab.taskId || tab.status !== "running") return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const task = await agentApi.getTaskStatus(tab.taskId!, tab.agentId);
        if (disposed) return;
        const phase = String(task.status || "running").toLowerCase();
        setTaskPhase(phase);
        const nestedPhase = String(task.result?.status || "").toLowerCase();
        const sessionId = task.result?.session_id;

        if (TERMINAL_FAILURE.has(phase) || TERMINAL_FAILURE.has(nestedPhase)) {
          syncTab({
            id: tab.id,
            sessionId,
            status: "failed",
            error:
              errorText(task.result?.error || task.error) ||
              t("subagentPanel.failed"),
          });
          return;
        }
        if (TERMINAL_SUCCESS.has(phase)) {
          syncTab({
            id: tab.id,
            sessionId,
            status:
              nestedPhase && TERMINAL_FAILURE.has(nestedPhase)
                ? "failed"
                : "completed",
          });
          return;
        }
      } catch (error) {
        if (!disposed) {
          setLoadError(error instanceof Error ? error.message : String(error));
        }
      }
      if (!disposed) timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [syncTab, t, tab.agentId, tab.id, tab.status, tab.taskId]);

  useEffect(() => {
    if (tab.status !== "completed" || !tab.sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const fetchPersistedHistory = async () => {
      attempts += 1;
      const loaded = await loadHistory();
      if (!disposed && !loaded && attempts < 20) {
        timer = setTimeout(fetchPersistedHistory, POLL_INTERVAL_MS);
      }
    };
    void fetchPersistedHistory();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [loadHistory, tab.sessionId, tab.status]);

  return (
    <div className={styles.tabContent}>
      <div className={styles.statusLine}>
        <Tag color={statusColor(tab.status)}>
          {t(`subagentPanel.status.${tab.status}`)}
        </Tag>
        {taskPhase && tab.status === "running" ? (
          <span className={styles.phase}>{taskPhase}</span>
        ) : null}
        <span className={styles.statusSpacer} />
        {tab.status === "completed" && tab.sessionId ? (
          <Tooltip title={t("common.refresh")}>
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              loading={loadingHistory}
              onClick={() => void loadHistory()}
            />
          </Tooltip>
        ) : null}
      </div>

      <div className={styles.taskBox}>
        <div className={styles.taskLabel}>{t("subagentPanel.task")}</div>
        <div>{tab.task || t("subagentPanel.unknownTask")}</div>
      </div>

      {tab.status === "running" ? (
        <div className={styles.runningState}>
          <Spin />
          <span>{t("subagentPanel.runningHint")}</span>
        </div>
      ) : null}

      {tab.status === "failed" ? (
        <Alert
          type="error"
          showIcon
          message={t("subagentPanel.failed")}
          description={tab.error}
        />
      ) : null}

      {loadError && tab.status === "completed" ? (
        <Alert type="info" showIcon message={loadError} />
      ) : null}

      {tab.status === "completed" && loadingHistory && !history ? (
        <div className={styles.runningState}>
          <Spin />
          <span>{t("subagentPanel.loadingHistory")}</span>
        </div>
      ) : null}

      {tab.status === "completed" && history ? (
        <MessageHistory messages={history.messages || []} />
      ) : null}
    </div>
  );
}

function SubagentSessionView({
  tab,
  active,
}: {
  tab: SubagentTab;
  active: boolean;
}) {
  const streamStatus = useSubagentStreamStore((state) =>
    tab.streamKey ? state.records[tab.streamKey]?.status : undefined,
  );

  useEffect(() => {
    if (!tab.streamKey) return;
    if (active) subagentStreamControllerRegistry.activate(tab.streamKey);
    else subagentStreamControllerRegistry.deactivate(tab.streamKey);
    return () => subagentStreamControllerRegistry.deactivate(tab.streamKey!);
  }, [active, tab.streamKey]);

  return (
    <div className={styles.tabScrollArea} data-subagent-scroll-container="true">
      {tab.streamKey && streamStatus !== "fallback" ? (
        <SubagentStreamView tab={tab} />
      ) : (
        <LegacySubagentSessionView tab={tab} />
      )}
    </div>
  );
}

export default function SubagentPanel() {
  const { t } = useTranslation();
  const open = useSubagentPanelStore((state) => state.open);
  const tabs = useSubagentPanelStore((state) => state.tabs);
  const activeTabId = useSubagentPanelStore((state) => state.activeTabId);
  const setActiveTab = useSubagentPanelStore((state) => state.setActiveTab);
  const closeTab = useSubagentPanelStore((state) => state.closeTab);
  const closePanel = useSubagentPanelStore((state) => state.closePanel);
  const currentParentSessionId = sessionApi.getSessionIdentity().sessionId;

  const items = useMemo(
    () =>
      tabs.map((tab, index) => ({
        key: tab.id,
        label: (
          <span className={styles.tabLabel} title={tab.sessionId || tab.task}>
            <RobotOutlined />
            <span>
              {tab.sessionId || `${t("subagentPanel.tab")} ${index + 1}`}
            </span>
          </span>
        ),
        children: (
          <SubagentSessionView
            tab={tab}
            active={
              open &&
              activeTabId === tab.id &&
              (!tab.parentSessionId ||
                tab.parentSessionId === currentParentSessionId)
            }
          />
        ),
        closable: true,
      })),
    [activeTabId, currentParentSessionId, open, t, tabs],
  );

  if (!open || tabs.length === 0) return null;

  return (
    <aside className={styles.panel} aria-label={t("subagentPanel.title")}>
      <div className={styles.header}>
        <span className={styles.headerTitle}>{t("subagentPanel.title")}</span>
        <Button
          type="text"
          size="small"
          icon={<CloseOutlined />}
          aria-label={t("common.close")}
          onClick={closePanel}
        />
      </div>
      <Tabs
        className={styles.tabs}
        type="editable-card"
        hideAdd
        activeKey={activeTabId || undefined}
        items={items}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") {
            const tab = tabs.find((item) => item.id === String(key));
            if (tab?.streamKey) {
              subagentStreamControllerRegistry.dispose(tab.streamKey);
            }
            closeTab(String(key));
          }
        }}
      />
    </aside>
  );
}
