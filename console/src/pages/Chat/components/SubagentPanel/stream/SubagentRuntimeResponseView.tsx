import { Alert, Image } from "antd";
import { Markdown, Thinking } from "@agentscope-ai/chat";
import type { ToolCallContent } from "../../../../../components/Chat/ToolCards/shared/types";
import {
  BUILTIN_CARD_REGISTRY,
  GenericToolCard,
} from "../../../../../components/Chat/ToolCards/cards";
import { SubagentRuntimeBuilderAdapter } from "./SubagentRuntimeBuilderAdapter";
import type { RuntimeResponseViewModel } from "./types";
import styles from "../index.module.less";

function contentData(message: Record<string, unknown>) {
  const content = Array.isArray(message.content)
    ? (message.content as Array<Record<string, unknown>>)
    : [];
  return content.map((item) => ({
    item,
    data:
      item.data && typeof item.data === "object"
        ? (item.data as Record<string, unknown>)
        : {},
  }));
}

function RuntimeMessage({ message }: { message: Record<string, unknown> }) {
  const blocks = contentData(message);
  return (
    <div className={styles.messageBubble}>
      {blocks.map(({ item }, index) => {
        const type = String(item.type || "");
        if (type === "text" && typeof item.text === "string") {
          return <Markdown key={index} content={item.text} baseFontSize={13} />;
        }
        if (type === "refusal" && typeof item.refusal === "string") {
          return (
            <Markdown key={index} content={item.refusal} baseFontSize={13} />
          );
        }
        if (type === "image" && typeof item.image_url === "string") {
          return <Image key={index} src={item.image_url} />;
        }
        if (type === "video" && typeof item.video_url === "string") {
          return <video key={index} src={item.video_url} controls />;
        }
        if (type === "audio") {
          const src = String(item.audio_url || item.data || "");
          return src ? <audio key={index} src={src} controls /> : null;
        }
        if (type === "file" && typeof item.file_url === "string") {
          return (
            <a
              key={index}
              href={item.file_url}
              target="_blank"
              rel="noreferrer"
            >
              {String(item.file_name || item.fileName || item.file_url)}
            </a>
          );
        }
        return <pre key={index}>{JSON.stringify(item, null, 2)}</pre>;
      })}
    </div>
  );
}

function RuntimeTool({ message }: { message: Record<string, unknown> }) {
  const blocks = contentData(message);
  const call = blocks[0]?.data || {};
  const output = blocks[1]?.data?.output;
  const name = String(call.name || "unknown");
  const rawArguments = call.arguments;
  let params: Record<string, unknown> = {};
  if (rawArguments && typeof rawArguments === "object") {
    params = rawArguments as Record<string, unknown>;
  } else if (typeof rawArguments === "string") {
    try {
      params = JSON.parse(rawArguments) as Record<string, unknown>;
    } catch {
      params = {};
    }
  }
  const status =
    String(message.status || "") === "in_progress" ? "calling" : "done";
  const content: ToolCallContent = {
    type: "tool_call",
    id: String(message.id || call.call_id || name),
    toolCallId: typeof call.call_id === "string" ? call.call_id : undefined,
    name,
    serverLabel:
      typeof call.server_label === "string" ? call.server_label : undefined,
    params,
    result: output,
    status,
  };
  const Card =
    name === "spawn_subagent"
      ? GenericToolCard
      : BUILTIN_CARD_REGISTRY[name] || GenericToolCard;
  return <Card content={content} isStreaming={status === "calling"} />;
}

export function SubagentRuntimeResponseView({
  data,
}: {
  data: RuntimeResponseViewModel;
}) {
  const messages = SubagentRuntimeBuilderAdapter.mergeToolMessages(
    data.output || [],
  );
  return (
    <div className={styles.messageList}>
      {messages.map((message, index) => {
        const type = String(message.type || "");
        const key = String(message.id || `${type}-${index}`);
        if (type === "heartbeat") return null;
        if (type === "message")
          return <RuntimeMessage key={key} message={message} />;
        if (type === "reasoning") {
          const text = String(contentData(message)[0]?.item.text || "");
          return (
            <Thinking
              key={key}
              title="Thinking"
              content={text}
              loading={String(message.status) === "in_progress"}
            />
          );
        }
        if (type.includes("call"))
          return <RuntimeTool key={key} message={message} />;
        if (type === "error") {
          return (
            <Alert
              key={key}
              type="error"
              showIcon
              message={String(message.code || "Subagent error")}
              description={String(message.message || "")}
            />
          );
        }
        return <pre key={key}>{JSON.stringify(message, null, 2)}</pre>;
      })}
    </div>
  );
}
