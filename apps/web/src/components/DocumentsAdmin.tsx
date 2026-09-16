"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Role } from "@harbor/chat-client";

import {
  type DocumentDetail,
  type DocumentSummary,
  type Visibility,
  deleteDocument,
  getDocument,
  listDocuments,
  reindexDocument,
  uploadDocument,
} from "@/lib/documents";

const VISIBILITY_HELP: Record<Visibility, string> = {
  public: "Customers on the website widget and WhatsApp can see this",
  internal: "Signed-in staff only",
  restricted: "Only the groups you pick below",
};

const STATUS_STYLE: Record<string, string> = {
  indexed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  pending: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  parsing: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

const BUSY: DocumentSummary["status"][] = ["pending", "parsing"];

export function DocumentsAdmin() {
  const [role, setRole] = useState<Role>("staff");
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [visibility, setVisibility] = useState<Visibility>("internal");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspecting, setInspecting] = useState<DocumentDetail | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [reloadToken, setReloadToken] = useState(0);
  const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

  // The guard matters: switching role mid-request would otherwise apply a stale response.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await listDocuments(role);
        if (!cancelled) setDocuments(result.documents);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [role, reloadToken]);

  // Ingestion is asynchronous, so poll while anything is still being parsed.
  useEffect(() => {
    if (!documents.some((document) => BUSY.includes(document.status))) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [documents, refresh]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDocument(role, {
        file,
        visibility,
        aclGroups: visibility === "restricted" ? ["management"] : undefined,
      });
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const act = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Upload PDF, Word, HTML, Markdown or text. Files are parsed, split and indexed in the
            background.
          </p>
        </div>
        <Link href="/" className="text-sm underline underline-offset-4">
          ← Back to chat
        </Link>
      </header>

      <form
        onSubmit={submit}
        className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
      >
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            aria-label="File to upload"
            className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-900 file:px-3 file:py-1.5 file:text-sm file:text-white dark:file:bg-zinc-100 dark:file:text-zinc-900"
          />
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-500">Visibility</span>
            <select
              value={visibility}
              onChange={(event) => setVisibility(event.target.value as Visibility)}
              className="rounded-md border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
            >
              <option value="public">public</option>
              <option value="internal">internal</option>
              <option value="restricted">restricted (management)</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-500">Upload as</span>
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
              className="rounded-md border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
            >
              <option value="staff">staff</option>
              <option value="management">management</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={!file || busy}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
        <p className="text-xs text-zinc-500">{VISIBILITY_HELP[visibility]}</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </form>

      <section className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-zinc-500">
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="px-4 py-3 font-medium">Document</th>
              <th className="px-2 py-3 font-medium">Access</th>
              <th className="px-2 py-3 font-medium">Status</th>
              <th className="px-2 py-3 font-medium">Chunks</th>
              <th className="px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.id} className="border-b border-zinc-100 dark:border-zinc-900">
                <td className="px-4 py-3">
                  <span className="block max-w-[20rem] truncate">{document.title}</span>
                  {document.error && (
                    <span className="block max-w-[26rem] text-xs text-red-600">
                      {document.error}
                    </span>
                  )}
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {document.visibility}
                </td>
                <td className="px-2 py-3">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[document.status]}`}
                  >
                    {document.status}
                  </span>
                </td>
                <td className="px-2 py-3 tabular-nums">{document.chunks}</td>
                <td className="flex flex-wrap gap-3 px-4 py-3 text-xs">
                  <button
                    type="button"
                    className="underline underline-offset-4"
                    onClick={() =>
                      void act(async () => setInspecting(await getDocument(document.id, role)))
                    }
                  >
                    Inspect
                  </button>
                  {document.has_original && (
                    <button
                      type="button"
                      className="underline underline-offset-4"
                      onClick={() => void act(() => reindexDocument(document.id, role))}
                    >
                      Re-index
                    </button>
                  )}
                  <button
                    type="button"
                    className="text-red-600 underline underline-offset-4"
                    onClick={() => void act(() => deleteDocument(document.id, role))}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {documents.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-sm text-zinc-500">
                  No documents yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {inspecting && (
        <aside
          role="dialog"
          aria-label="Indexed chunks"
          className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
        >
          <header className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-medium">{inspecting.title}</h2>
              <p className="text-xs text-zinc-500">
                {inspecting.chunks.length} indexed passage(s) · version {inspecting.version} · what
                retrieval actually searches
              </p>
            </div>
            <button
              type="button"
              onClick={() => setInspecting(null)}
              aria-label="Close"
              className="rounded px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              ✕
            </button>
          </header>
          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
            {inspecting.chunks.map((chunk) => (
              <article key={chunk.id} className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
                <p className="mb-1 text-xs text-zinc-500">
                  #{chunk.ordinal} · {chunk.heading_path.join(" › ") || "(no heading)"} ·{" "}
                  {chunk.token_count} tokens
                </p>
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{chunk.text}</p>
              </article>
            ))}
          </div>
        </aside>
      )}
    </div>
  );
}
