import { connection } from "next/server";

import { getApiStatus } from "@/lib/api-status";

export default async function StatusPage() {
  // Status is request-time data; keep it out of build-time prerendering.
  await connection();
  const status = await getApiStatus(process.env.HARBOR_API_URL ?? "http://localhost:8000");

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-24">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Harbor</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Bilingual knowledge assistant · 雙語知識助手
        </p>
      </header>

      <section
        aria-labelledby="api-status"
        className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800"
      >
        <h2 id="api-status" className="mb-3 text-sm font-medium uppercase text-zinc-500">
          API status
        </h2>
        {status.reachable ? (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
            <dt className="text-zinc-500">version</dt>
            <dd>
              {status.version} ({status.env})
            </dd>
            {Object.entries(status.checks).map(([name, result]) => (
              <div key={name} className="contents">
                <dt className="text-zinc-500">{name}</dt>
                <dd className={result === "ok" ? "text-emerald-600" : "text-red-600"}>{result}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-red-600">Unreachable: {status.error}</p>
        )}
      </section>
    </main>
  );
}
