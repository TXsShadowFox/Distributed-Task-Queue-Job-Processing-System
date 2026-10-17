import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  // Self-contained server bundle for the Docker image (node server.js, no node_modules at runtime).
  output: "standalone",
  poweredByHeader: false,
  // Keep the dev-only indicator clear of the sidebar controls.
  devIndicators: { position: "bottom-right" },
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
