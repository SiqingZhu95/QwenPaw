export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

function parseFrame(raw: string): SseFrame | null {
  const data: string[] = [];
  let id: string | undefined;
  let event: string | undefined;
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data.push(value);
    else if (field === "id") id = value;
    else if (field === "event") event = value;
  }
  if (data.length === 0) return null;
  return { id, event, data: data.join("\n") };
}

export class SseFrameParser {
  private buffer = "";

  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    while (true) {
      const match = /\r?\n\r?\n/.exec(this.buffer);
      if (!match || match.index === undefined) break;
      const raw = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  finish(): SseFrame[] {
    const raw = this.buffer;
    this.buffer = "";
    const frame = parseFrame(raw);
    return frame ? [frame] : [];
  }
}
