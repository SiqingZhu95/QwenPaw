import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SubagentMessageScroller } from "./SubagentMessageScroller";
import styles from "./index.module.less";

describe("SubagentMessageScroller", () => {
  it("keeps the main chat message-list scroll structure", () => {
    const { container, getByText } = render(
      <SubagentMessageScroller>
        <div>long subagent output</div>
      </SubagentMessageScroller>,
    );

    const scroll = container.querySelector(
      '[data-subagent-message-scroll="true"]',
    );
    expect(scroll).toHaveClass(styles.subagentBubbleListScroll);
    expect(scroll?.parentElement).toHaveClass(styles.subagentBubbleListWrapper);
    expect(scroll?.parentElement?.parentElement).toHaveClass(
      styles.subagentMessageList,
    );
    expect(scroll?.parentElement?.parentElement?.parentElement).toHaveClass(
      styles.subagentChat,
    );
    expect(getByText("long subagent output")).toBeInTheDocument();

    const messageList = scroll?.parentElement?.parentElement;
    expect(getComputedStyle(messageList!).height).toBe("0px");
    expect(getComputedStyle(scroll!).height).toBe("100%");
    expect(getComputedStyle(scroll!).overflow).toBe("auto");
  });
});
