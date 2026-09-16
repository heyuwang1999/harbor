export type ApiStatus =
  | { reachable: true; version: string; env: string; checks: Record<string, "ok" | "fail"> }
  | { reachable: false; error: string };

type Fetch = typeof fetch;

/** Server-side probe of the Harbor API, used by the M0 status page. Never throws. */
export async function getApiStatus(
  baseUrl: string,
  fetchImpl: Fetch = fetch,
  timeoutMs = 2000,
): Promise<ApiStatus> {
  try {
    const signal = AbortSignal.timeout(timeoutMs);
    const [metaRes, readyRes] = await Promise.all([
      fetchImpl(`${baseUrl}/v1/meta`, { signal, cache: "no-store" }),
      fetchImpl(`${baseUrl}/readyz`, { signal, cache: "no-store" }),
    ]);
    if (!metaRes.ok) {
      return { reachable: false, error: `meta returned HTTP ${metaRes.status}` };
    }
    const meta = (await metaRes.json()) as { version: string; env: string };
    // /readyz returns 503 with a body when a dependency is down; that is still useful data.
    const ready = (await readyRes.json()) as { checks: Record<string, "ok" | "fail"> };
    return { reachable: true, version: meta.version, env: meta.env, checks: ready.checks };
  } catch (error) {
    return { reachable: false, error: error instanceof Error ? error.message : String(error) };
  }
}
