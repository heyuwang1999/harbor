import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server for the Docker image (see Dockerfile).
  output: "standalone",
  // Monorepo: trace dependencies from the repo root.
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
};

export default nextConfig;
