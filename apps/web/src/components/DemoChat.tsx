"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { type Citation, type DoneEvent, type Role, streamChat } from "@harbor/chat-client";

import { API_BASE_URL } from "@/lib/config";
import { SampleQuestions } from "@/components/SampleQuestions";
import { SourceDrawer } from "@/components/SourceDrawer";
import { TracePanel } from "@/components/TracePanel";

type Turn = {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  done: DoneEvent | null;
  status: string;
  error?: string;
};

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "visitor", label: "Visitor", hint: "Website / WhatsApp customer: public documents only" },
  { value: "staff", label: "Staff", hint: "Signed-in employee: public + internal documents" },
  { value: "management", label: "Management", hint: "Adds restricted documents (salary bands)" },
];

const ANSWER_BADGE: Record<string, string> = {
  answered: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  refused: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  error: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

/** Render an answer with its [S#] markers as clickable chips. */
function AnswerText({
  text,
  citations,
  onSelect,
}: {
  text: string;
  citations: Citation[];
  onSelect: (citation: Citation) => void;
}) {
  const parts = text.split(/(\[S\d+\])/g);
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed">
      {parts.map((part, index) => {
        const match = /^\[(S\d+)\]$/.exec(part);
        const citation = match ? citations.find((item) => item.marker === match[1]) : undefined;
        if (!citation) return <span key={index}>{part}</span>;
        return (
          <button
            key={index}
            type="button"
            onClick={() => onSelect(citation)}
            title={`${citation.document_title} · ${citation.heading_path.join(" › ")}`}
            className="mx-0.5 rounded bg-blue-100 px-1.5 py-0.5 align-baseline text-xs font-medium text-blue-800 transition hover:bg-blue-200 dark:bg-blue-950 dark:text-blue-300"
          >
            {citation.marker}
          </button>
        );
      })}
    </p>
  );
}

export function DemoChat() {
  const [role, setRole] = useState<Role>("staff");
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Citation | null>(null);
  const [mock, setMock] = useState(true);
  const conversationRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const ask = useCallback(
    async (question: string) => {
      if (!question.trim() || busy) return;
      const id = crypto.randomUUID();
      setBusy(true);
      setInput("");
      setTurns((current) => [
        ...current,
        { id, question, answer: "", citations: [], done: null, status: "sending" },
      ]);

      const update = (patch: Partial<Turn>) =>
        setTurns((current) =>
          current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
        );

      try {
        for await (const event of streamChat({
          baseUrl: API_BASE_URL,
          message: question,
          role,
          conversationId: conversationRef.current,
        })) {
          switch (event.name) {
            case "meta":
              conversationRef.current = event.data.conversation_id;
              setMock(event.data.mock);
              break;
            case "status":
              update({ status: event.data.stage });
              break;
            case "delta":
              setTurns((current) =>
                current.map((turn) =>
                  turn.id === id ? { ...turn, answer: turn.answer + event.data.text } : turn,
                ),
              );
              break;
            case "citations":
              update({ citations: event.data.citations });
              break;
            case "done":
              // The server's validated text replaces the streamed draft.
              update({ answer: event.data.text, done: event.data, status: "done" });
              break;
            case "error":
              update({ error: event.data.message, status: "error" });
              break;
          }
        }
      } catch (error) {
        update({ error: error instanceof Error ? error.message : String(error), status: "error" });
      } finally {
        setBusy(false);
      }
    },
    [busy, role],
  );

  const latest = turns.at(-1);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Harbor</h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              海港餅店 · knowledge assistant with cited answers
            </p>
          </div>
          <div className="flex items-center gap-3">
            {mock && (
              <span className="rounded-full bg-zinc-200 px-3 py-1 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                mock model · answers quote the retrieved passages verbatim
              </span>
            )}
            <Link href="/documents" className="text-sm underline underline-offset-4">
              Documents →
            </Link>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-zinc-500">Viewing as</span>
          <div
            role="radiogroup"
            aria-label="Demo role"
            className="inline-flex overflow-hidden rounded-lg border border-zinc-300 dark:border-zinc-700"
          >
            {ROLES.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={role === option.value}
                title={option.hint}
                onClick={() => setRole(option.value)}
                className={`px-3 py-1.5 text-sm transition ${
                  role === option.value
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span className="text-xs text-zinc-500">
            {ROLES.find((option) => option.value === role)?.hint}
          </span>
        </div>

        <SampleQuestions role={role} disabled={busy} onPick={ask} />
      </header>

      <div className="flex flex-1 flex-col gap-4">
        {turns.length === 0 && (
          <p className="rounded-lg border border-dashed border-zinc-300 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
            Ask in English, 繁體中文, 简体中文 or Cantonese. Answers come only from the demo
            company&apos;s documents, with citations you can open.
          </p>
        )}

        {turns.map((turn) => (
          <article key={turn.id} className="flex flex-col gap-3">
            <p className="self-end rounded-2xl rounded-br-sm bg-zinc-900 px-4 py-2 text-sm text-white dark:bg-zinc-100 dark:text-zinc-900">
              {turn.question}
            </p>

            <div className="rounded-2xl rounded-bl-sm border border-zinc-200 px-4 py-3 dark:border-zinc-800">
              {turn.error ? (
                <p className="text-sm text-red-600">{turn.error}</p>
              ) : turn.answer ? (
                <AnswerText text={turn.answer} citations={turn.citations} onSelect={setSelected} />
              ) : (
                <p className="text-sm text-zinc-500">
                  {turn.status === "retrieving" ? "Searching documents…" : "Writing answer…"}
                </p>
              )}

              {turn.done && (
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                  <span
                    className={`rounded px-2 py-0.5 font-medium ${ANSWER_BADGE[turn.done.answer_type]}`}
                  >
                    {turn.done.answer_type}
                  </span>
                  <span>confidence {turn.done.confidence.toFixed(2)}</span>
                  <span>{turn.done.latency_ms} ms</span>
                  {turn.done.dropped_citations > 0 && (
                    <span className="text-amber-600">
                      {turn.done.dropped_citations} invalid citation(s) removed
                    </span>
                  )}
                  {turn.done.stripped_links > 0 && (
                    <span className="text-amber-600">
                      {turn.done.stripped_links} link(s) stripped
                    </span>
                  )}
                </div>
              )}
            </div>
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(input);
        }}
        className="sticky bottom-4 flex gap-2 rounded-xl border border-zinc-300 bg-white p-2 shadow-sm dark:border-zinc-700 dark:bg-zinc-950"
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="問一個關於公司文件的問題… / Ask about the company documents…"
          aria-label="Your question"
          className="flex-1 bg-transparent px-2 py-2 text-sm outline-none"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {busy ? "…" : "Ask"}
        </button>
      </form>

      {latest?.done && <TracePanel done={latest.done} />}

      {selected && <SourceDrawer citation={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
