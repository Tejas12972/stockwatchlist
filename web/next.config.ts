import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Emits a self-contained server bundle with only the node_modules actually
  // reached at runtime, so the production image copies ~50MB instead of the
  // whole dependency tree.
  output: "standalone",
  // Nothing environment-specific is compiled in. The API address is read at
  // request time by app/api/[...path]/route.ts from API_INTERNAL_URL, so one
  // built image runs unchanged against local, staging or production.
};

export default config;
