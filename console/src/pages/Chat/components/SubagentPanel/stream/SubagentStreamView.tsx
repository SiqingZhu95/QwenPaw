import { Alert, Empty, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";
import type { SubagentTab } from "../../../../../stores/subagentPanelStore";
import { SubagentStreamErrorBoundary } from "./SubagentStreamErrorBoundary";
import { useSubagentStreamStore } from "./subagentStreamStore";
import { SubagentRuntimeResponseView } from "./SubagentRuntimeResponseView";
import styles from "../index.module.less";

export function SubagentStreamView({ tab }: { tab: SubagentTab }) {
  const { t } = useTranslation();
  const record = useSubagentStreamStore((state) =>
    tab.streamKey ? state.records[tab.streamKey] : undefined,
  );
  const status = record?.status || "resolving";
  const failed = status === "failed";
  const completed = status === "completed";

  return (
    <div className={styles.tabContent}>
      <div className={styles.statusLine}>
        <Tag color={failed ? "error" : completed ? "success" : "processing"}>
          {failed
            ? t("subagentPanel.status.failed")
            : completed
            ? t("subagentPanel.status.completed")
            : t("subagentPanel.status.running")}
        </Tag>
        <span className={styles.phase}>{status}</span>
      </div>
      <div className={styles.taskBox}>
        <div className={styles.taskLabel}>{t("subagentPanel.task")}</div>
        <div>{tab.task || t("subagentPanel.unknownTask")}</div>
      </div>
      {record?.viewModel ? (
        <SubagentStreamErrorBoundary
          resetKey={`${record.streamId || ""}:${record.lastSequence}`}
        >
          <SubagentRuntimeResponseView data={record.viewModel} />
        </SubagentStreamErrorBoundary>
      ) : failed ? (
        <Alert
          type="error"
          showIcon
          message={t("subagentPanel.failed")}
          description={record?.errorCode}
        />
      ) : (
        <div className={styles.runningState}>
          <Spin />
          <span>{t("subagentPanel.runningHint")}</span>
        </div>
      )}
      {completed && !record?.viewModel ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : null}
    </div>
  );
}
