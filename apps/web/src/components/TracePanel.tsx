"use client";

import type { DoneEvent } from "@harbor/chat-client";

const VISIBILITY_STYLE: Record<string, string> = {
  public: "text-emerald-600 dark:text-emerald-400",
  internal: "text-amber-600 dark:text-amber-400",
  restricted: "text-red-600 dark:text-red-400",
};

function format(value: number | null, digits = 2) {
  return value === null || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

/** Why these passages: the per-stage ranks behind the answer, straight from the API. */
export function TracePanel({ done }: { done: DoneEvent }) {
  const strategy = done.strategy as { vector_search?: string; tenant_chunks?: number };

  return (
    <section className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-medium">Retrieval trace</h2>
        <p className="text-xs text-zinc-500">
          {strategy.vector_search === "exact" ? "exact vector search" : "HNSW + iterative scan"} ·{" "}
          {strategy.tenant_chunks} chunks · {done.latency_ms} ms
        </p>
      </header>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 px-4 py-3 text-xs sm:grid-cols-4">
        {Object.entries(done.timings_ms).map(([stage, ms]) => (
          <div key={stage} className="flex justify-between gap-2">
            <dt className="text-zinc-500">{stage}</dt>
            <dd className="tabular-nums">{ms} ms</dd>
          </div>
        ))}
        <div className="flex justify-between gap-2">
          <dt className="text-zinc-500">tokens</dt>
          <dd className="tabular-nums">{done.usage.total_tokens ?? "—"}</dd>
        </div>
      </dl>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-left text-xs">
          <thead className="text-zinc-500">
            <tr className="border-t border-zinc-200 dark:border-zinc-800">
              <th className="px-4 py-2 font-medium">Passage</th>
              <th className="px-2 py-2 font-medium">Access</th>
              <th className="px-2 py-2 font-medium">BM25</th>
              <th className="px-2 py-2 font-medium">Vector</th>
              <th className="px-4 py-2 font-medium">Fused</th>
            </tr>
          </thead>
          <tbody>
            {done.trace.map((candidate) => (
              <tr
                key={candidate.chunk_id}
                className="border-t border-zinc-100 align-top dark:border-zinc-900"
              >
                <td className="px-4 py-2">
                  <span className="block max-w-[18rem] truncate">
                    {candidate.heading_path.at(-1) ?? candidate.document_title}
                  </span>
                  <span className="block max-w-[18rem] truncate text-zinc-500">
                    {candidate.document_title}
                  </span>
                </td>
                <td className={`px-2 py-2 ${VISIBILITY_STYLE[candidate.visibility] ?? ""}`}>
                  {candidate.visibility}
                </td>
                <td className="px-2 py-2 tabular-nums">
                  {candidate.bm25_rank ? `#${candidate.bm25_rank}` : "—"}{" "}
                  <span className="text-zinc-500">{format(candidate.bm25_score, 1)}</span>
                </td>
                <td className="px-2 py-2 tabular-nums">
                  {candidate.vector_rank ? `#${candidate.vector_rank}` : "—"}{" "}
                  <span className="text-zinc-500">{format(candidate.similarity, 3)}</span>
                </td>
                <td className="px-4 py-2 tabular-nums">{format(candidate.fused_score, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
