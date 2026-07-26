import type { ReactNode } from "react";
import styles from "./index.module.less";

interface SubagentMessageScrollerProps {
  children: ReactNode;
}

/**
 * Mirrors the main chat's SDK scroll layout:
 * chat -> message-list(height: 0) -> wrapper -> scroll.
 */
export function SubagentMessageScroller({
  children,
}: SubagentMessageScrollerProps) {
  return (
    <div className={styles.subagentChat}>
      <div className={styles.subagentMessageList}>
        <div className={styles.subagentBubbleListWrapper}>
          <div
            className={styles.subagentBubbleListScroll}
            data-subagent-message-scroll="true"
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
