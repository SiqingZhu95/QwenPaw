export function extractMessageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return content == null ? "" : String(content);
  return content
    .map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return "";
      const block = item as Record<string, unknown>;
      if (typeof block.text === "string") return block.text;
      if (typeof block.output === "string") return block.output;
      if (typeof block.content === "string") return block.content;
      const type = String(block.type || "");
      if (type.includes("tool") || type.includes("plugin")) {
        try {
          return `\`\`\`json\n${JSON.stringify(block, null, 2)}\n\`\`\``;
        } catch {
          return String(block);
        }
      }
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}
