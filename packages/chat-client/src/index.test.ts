import { describe, expect, it } from "vitest";

import { parseSSE } from "./index";

describe("parseSSE", () => {
  it("parses complete events and keeps the trailing partial frame", () => {
    const buffer =
      'event: meta\ndata: {"conversation_id":"c1"}\n\n' +
      'event: delta\ndata: {"text":"病假"}\n\n' +
      'event: delta\ndata: {"text":"證明';

    const { events, rest } = parseSSE(buffer);

    expect(events).toEqual([
      { name: "meta", data: { conversation_id: "c1" } },
      { name: "delta", data: { text: "病假" } },
    ]);
    expect(rest).toBe('event: delta\ndata: {"text":"證明');
  });

  it("returns nothing for an empty buffer", () => {
    expect(parseSSE("")).toEqual({ events: [], rest: "" });
  });

  it("skips malformed frames instead of throwing", () => {
    const { events } = parseSSE('event: delta\ndata: {not json}\n\nevent: done\ndata: {"ok":1}\n\n');

    expect(events).toEqual([{ name: "done", data: { ok: 1 } }]);
  });
});
