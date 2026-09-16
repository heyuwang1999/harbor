import type { Role } from "@harbor/chat-client";

import { API_BASE_URL } from "@/lib/config";

export type DocumentStatus = "pending" | "parsing" | "indexed" | "failed";
export type Visibility = "public" | "internal" | "restricted";

export interface DocumentSummary {
  id: string;
  title: string;
  language: string;
  visibility: Visibility;
  status: DocumentStatus;
  error: string | null;
  has_original: boolean;
  chunks: number;
  updated_at: string;
}

export interface DocumentChunk {
  id: string;
  ordinal: number;
  heading_path: string[];
  text: string;
  token_count: number;
}

export interface DocumentDetail extends Omit<DocumentSummary, "chunks" | "has_original"> {
  version: number;
  chunks: DocumentChunk[];
}

async function request<T>(path: string, role: Role, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), "x-demo-role": role },
  });
  if (!response.ok) {
    // The API returns a human-readable reason for rejected uploads; surface it as-is.
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `Request failed (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function listDocuments(role: Role) {
  return request<{ documents: DocumentSummary[]; tenant: { name: string } }>(
    "/v1/documents",
    role,
    { cache: "no-store" },
  );
}

export function getDocument(id: string, role: Role) {
  return request<DocumentDetail>(`/v1/documents/${id}`, role, { cache: "no-store" });
}

export function uploadDocument(
  role: Role,
  input: { file: File; visibility: Visibility; title?: string; aclGroups?: string[] },
) {
  const form = new FormData();
  form.set("file", input.file);
  form.set("visibility", input.visibility);
  if (input.title) form.set("title", input.title);
  if (input.aclGroups?.length) form.set("acl_groups", input.aclGroups.join(","));
  return request<{ id: string; status: DocumentStatus }>("/v1/documents", role, {
    method: "POST",
    body: form,
  });
}

export function reindexDocument(id: string, role: Role) {
  return request<{ id: string }>(`/v1/documents/${id}/reindex`, role, { method: "POST" });
}

export function deleteDocument(id: string, role: Role) {
  return request<void>(`/v1/documents/${id}`, role, { method: "DELETE" });
}
