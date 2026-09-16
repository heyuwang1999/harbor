/**
 * Framework-free client for Harbor's chat stream.
 *
 * Lives in its own package because three surfaces consume the same contract: the staff
 * web app today, the embeddable widget in M3, and the test suite. The event shapes here
 * mirror docs/specs/chat-stream.md.
 */

export type Role = "visitor" | "staff" | "management";

export interface Citation {
  marker: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  heading_path: string[];
  text: string;
}

export interface TraceCandidate {
  chunk_id: string;
  document_title: string;
  heading_path: string[];
  visibility: string;
  bm25_rank: number | null;
  bm25_score: number | null;
  vector_rank: number | null;
  similarity: number | null;
  fused_score: number;
}

export interface MetaEvent {
  conversation_id: string;
  language: string;
  role: Role;
  model: string;
  mock: boolean;
}

export interface DoneEvent {
  message_id: string;
  conversation_id: string;
  trace_id: string;
  /** Authoritative answer: deltas are raw model output, this survived validation. */
  text: string;
  answer_type: "answered" | "refused" | "error";
  confidence: number;
  dropped_citations: number;
  stripped_links: number;
  model: string | null;
  usage: Record<string, number>;
  latency_ms: number;
  timings_ms: Record<string, number>;
  strategy: Record<string, unknown>;
  trace: TraceCandidate[];
}

export type ChatEvent =
  | { name: "meta"; data: MetaEvent }
  | { name: "status"; data: { stage: string; retrieved?: number; candidates?: number } }
  | { name: "delta"; data: { text: string } }
  | { name: "citations"; data: { citations: Citation[] } }
  | { name: "done"; data: DoneEvent }
  | { name: "error"; data: { message: string } };

export interface ChatOptions {
  baseUrl: string;
  message: string;
  role: Role;
  conversationId?: string | null;
  visitorId?: string;
  channel?: string;
  signal?: AbortSignal;
}

/** Parse a raw SSE body into events. Exported for tests and reuse by the widget. */
export function parseSSE(buffer: string): { events: ChatEvent[]; rest: string } {
  const events: ChatEvent[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";

  for (const part of parts) {
    let name = "message";
    const dataLines: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      events.push({ name, data: JSON.parse(dataLines.join("\n")) } as ChatEvent);
    } catch {
      // A partial frame can only happen if the server splits mid-event; skip it rather
      // than killing the stream.
    }
  }
  return { events, rest };
}

export async function* streamChat(options: ChatOptions): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${options.baseUrl}/v1/chat`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-demo-role": options.role },
    body: JSON.stringify({
      message: options.message,
      channel: options.channel ?? "web",
      visitor_id: options.visitorId ?? "demo",
      conversation_id: options.conversationId ?? null,
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(`chat request failed (${response.status}): ${detail.slice(0, 200)}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSSE(buffer);
    buffer = rest;
    for (const event of events) yield event;
  }
}
