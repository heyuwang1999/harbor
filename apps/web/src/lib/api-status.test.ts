import { describe, expect, it, vi } from "vitest";

import { getApiStatus } from "./api-status";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("getApiStatus", () => {
  it("combines meta and readiness when the API is healthy", async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request) =>
      String(url).endsWith("/v1/meta")
        ? json({ name: "harbor", version: "0.1.0", env: "dev" })
        : json({ status: "ok", checks: { database: "ok", redis: "ok" } }),
    );

    await expect(getApiStatus("http://api", fetchMock as typeof fetch)).resolves.toEqual({
      reachable: true,
      version: "0.1.0",
      env: "dev",
      checks: { database: "ok", redis: "ok" },
    });
  });

  it("reports failed dependencies from a 503 readiness response", async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request) =>
      String(url).endsWith("/v1/meta")
        ? json({ name: "harbor", version: "0.1.0", env: "dev" })
        : json({ status: "degraded", checks: { database: "ok", redis: "fail" } }, 503),
    );

    const status = await getApiStatus("http://api", fetchMock as typeof fetch);
    expect(status).toMatchObject({ reachable: true, checks: { redis: "fail" } });
  });

  it("never throws when the API is unreachable", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });

    await expect(getApiStatus("http://api", fetchMock as typeof fetch)).resolves.toEqual({
      reachable: false,
      error: "fetch failed",
    });
  });
});
