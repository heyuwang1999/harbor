# Chat stream contract

`POST /v1/chat` returns `text/event-stream`. The web app, the M3 widget and the tests all
speak this contract through `packages/chat-client`.

## Request

```jsonc
{
  "message": "請病假要唔要醫生紙？",
  "channel": "web",              // web | widget | whatsapp
  "visitor_id": "demo",          // rate-limit key
  "conversation_id": null        // null starts a new conversation
}
```

Headers: `x-demo-role: visitor | staff | management` selects the access scope. **Demo only** —
M2 replaces it with a Better Auth session, and the server rejects the header outside demo mode.

Rejections happen before the stream opens, as normal HTTP: `413` (message too long),
`429` (rate limit, with `retry-after`), `400` (unknown role).

## Events

Emitted in this order. Every `data` payload is a JSON object.

| Event | When | Payload |
|---|---|---|
| `meta` | immediately | `conversation_id`, `language`, `role`, `model`, `mock` |
| `status` | before each phase | `stage`: `retrieving` \| `generating`, plus `retrieved` / `candidates` counts |
| `delta` | repeatedly while generating | `text`: raw model output fragment |
| `citations` | after generation | `citations[]`: `marker`, `chunk_id`, `document_id`, `document_title`, `heading_path`, `text` |
| `done` | last | see below |
| `error` | instead of further deltas | `message` |

### `done`

```jsonc
{
  "message_id": "…", "conversation_id": "…", "trace_id": "…",
  "text": "…",                  // AUTHORITATIVE answer — see below
  "answer_type": "answered",    // answered | refused | error
  "confidence": 0.87,
  "dropped_citations": 0,        // markers pointing at sources that were never supplied
  "stripped_links": 0,           // links removed from the answer
  "model": "mock-main",
  "usage": { "prompt_tokens": 396, "completion_tokens": 98, "total_tokens": 494 },
  "latency_ms": 155,
  "timings_ms": { "embed": 7.2, "bm25": 111.6, "vector": 4.2 },
  "strategy": { "vector_search": "exact", "expanded_terms": ["台风信号"], "role": "staff", … },
  "trace": [ { "chunk_id": "…", "bm25_rank": 1, "vector_rank": 4, "fused_score": 0.032, … } ]
}
```

**`done.text` replaces whatever the deltas produced.** Deltas are raw model output; the
final text is what survived citation validation and link stripping. A client that renders
deltas must swap in `done.text` when it arrives — `DemoChat.tsx` does exactly that.

A refusal arrives as a single `delta` plus `answer_type: "refused"`: the gate runs before
the model, so nothing is generated when retrieval found no supporting passage.

## Notes

- Answers cite sources as `[S1]`, `[S2]` … matching `citations[].marker`.
- The stream is not resumable yet; `Last-Event-ID` support lands with the widget in M3.
- No token is ever emitted for a passage the caller may not read: the access scope is
  applied inside the retrieval SQL, before generation.
