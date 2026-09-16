/**
 * End-to-end check of the browser code path against a running API.
 *
 * Skipped unless HARBOR_LIVE_API points at one (`make infra && make dev-api`), so the
 * default test run stays offline. It covers what unit tests cannot: that the server's
 * SSE framing, event order and validated final text match what the UI expects.
 */

import { describe, expect, it } from "vitest";

import { type Citation, type DoneEvent, streamChat } from "./index";

const baseUrl = process.env.HARBOR_LIVE_API;
const live = baseUrl ? describe : describe.skip;

live("streamChat against a live API", () => {
  it("streams a grounded answer with citations for a Cantonese question", async () => {
    const seen: string[] = [];
    let streamed = "";
    let citations: Citation[] = [];
    let done: DoneEvent | undefined;

    for await (const event of streamChat({
      baseUrl: baseUrl!,
      message: "請病假要唔要醫生紙？",
      role: "staff",
    })) {
      seen.push(event.name);
      if (event.name === "delta") streamed += event.data.text;
      if (event.name === "citations") citations = event.data.citations;
      if (event.name === "done") done = event.data;
    }

    expect(seen[0]).toBe("meta");
    expect(seen).toContain("status");
    expect(seen.at(-1)).toBe("done");
    expect(streamed.length).toBeGreaterThan(0);
    expect(done?.answer_type).toBe("answered");
    expect(done?.text).toContain("醫生證明書");
    expect(citations[0]?.marker).toBe("S1");
    expect(citations[0]?.text).toContain("醫生證明書");
    expect(done?.trace.length).toBeGreaterThan(0);
  }, 30_000);

  it("refuses a question the documents do not cover", async () => {
    let done: DoneEvent | undefined;
    for await (const event of streamChat({
      baseUrl: baseUrl!,
      message: "邊個發明咗小籠包？",
      role: "staff",
    })) {
      if (event.name === "done") done = event.data;
    }

    expect(done?.answer_type).toBe("refused");
    expect(done?.text).toContain("找不到");
  }, 30_000);

  it("keeps restricted passages away from a visitor", async () => {
    let done: DoneEvent | undefined;
    for await (const event of streamChat({
      baseUrl: baseUrl!,
      message: "分店經理的月薪範圍是多少？",
      role: "visitor",
    })) {
      if (event.name === "done") done = event.data;
    }

    expect(done?.answer_type).toBe("refused");
    expect(done?.trace.every((candidate) => candidate.visibility === "public")).toBe(true);
  }, 30_000);
});
