"use client";

import type { Role } from "@harbor/chat-client";

type Sample = { label: string; question: string; note: string; roles: Role[] };

/** Chosen to show the four behaviours worth demonstrating: bilingual retrieval,
 * an honest refusal, and access control from both sides. */
const SAMPLES: Sample[] = [
  {
    label: "粵語 · 病假",
    question: "請病假要唔要醫生紙？",
    note: "Cantonese question, Traditional Chinese source",
    roles: ["staff", "management"],
  },
  {
    label: "简体 · 强积金",
    question: "强积金供款比例是多少？",
    note: "Simplified query against Traditional documents",
    roles: ["staff", "management"],
  },
  {
    label: "English · probation",
    question: "How long is the probation period and the notice needed?",
    note: "English question, English handbook",
    roles: ["visitor", "staff", "management"],
  },
  {
    label: "顧客 · 訂蛋糕",
    question: "訂造蛋糕要幾多日前落單？",
    note: "Public FAQ: answerable for everyone",
    roles: ["visitor", "staff", "management"],
  },
  {
    label: "權限 · 薪酬",
    question: "分店經理的月薪範圍是多少？",
    note: "Restricted: only management gets an answer",
    roles: ["visitor", "staff", "management"],
  },
  {
    label: "拒答 · 範圍外",
    question: "邊個發明咗小籠包？",
    note: "Outside the corpus: Harbor refuses instead of guessing",
    roles: ["visitor", "staff", "management"],
  },
];

export function SampleQuestions({
  role,
  disabled,
  onPick,
}: {
  role: Role;
  disabled: boolean;
  onPick: (question: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {SAMPLES.filter((sample) => sample.roles.includes(role)).map((sample) => (
        <button
          key={sample.question}
          type="button"
          disabled={disabled}
          title={`${sample.question} — ${sample.note}`}
          onClick={() => onPick(sample.question)}
          className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-700 transition hover:border-zinc-500 hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        >
          {sample.label}
        </button>
      ))}
    </div>
  );
}
