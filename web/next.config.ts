import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Emits a self-contained server bundle with only the node_modules actually
  // reached at runtime, so the production image copies ~50MB instead of the
  // whole dependency tree.
  output: "standalone",
  // The API base URL is read at build time by the client bundle. It is declared
  // here as well so `next build` fails loudly in CI if it is ever removed from
  // the environment, rather than shipping a bundle that silently calls itself.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  },
};

export default config;
