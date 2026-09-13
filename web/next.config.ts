import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The API base URL is read at build time by the client bundle. It is declared
  // here as well so `next build` fails loudly in CI if it is ever removed from
  // the environment, rather than shipping a bundle that silently calls itself.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  },
};

export default config;
