"use client";

import type { Citation } from "@harbor/chat-client";

/** The cited passage, exactly as stored. Clicking a citation must show the evidence,
 * not a summary of it. */
export function SourceDrawer({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  return (
    <aside
      role="dialog"
      aria-label="Cited passage"
      className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md flex-col border-l border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
    >
      <header className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-zinc-500">{citation.marker}</p>
          <h2 className="truncate text-sm font-medium">{citation.document_title}</h2>
          <p className="truncate text-xs text-zinc-500">{citation.heading_path.join(" › ")}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          ✕
        </button>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4">
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{citation.text}</p>
      </div>
    </aside>
  );
}
