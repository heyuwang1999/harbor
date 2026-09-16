# syntax=docker/dockerfile:1
# Build context: repository root (pnpm workspace).
FROM node:22-bookworm-slim AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /repo
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
# Manifests first so dependency installation stays cached when only sources change.
COPY apps/web/package.json apps/web/
COPY packages/chat-client/package.json packages/chat-client/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile --filter @harbor/web...
# The app imports @harbor/chat-client sources directly (next transpilePackages).
COPY packages/chat-client packages/chat-client
COPY apps/web apps/web
RUN pnpm --filter @harbor/web build

FROM node:22-bookworm-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
# Pick up base-image security fixes (Trivy gates the build on HIGH/CRITICAL).
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
# The runtime only runs `node server.js`; the bundled npm/corepack CLIs are unused
# and carry their own CVEs, so drop them.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
RUN useradd --create-home --uid 10001 harbor
COPY --from=build --chown=harbor:harbor /repo/apps/web/.next/standalone ./
COPY --from=build --chown=harbor:harbor /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=harbor:harbor /repo/apps/web/public ./apps/web/public
USER harbor
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
