import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The end-to-end suite builds into its own directory. Sharing .next with a running
  // dev server corrupts it: the suite wipes and rebuilds, the dev server keeps its module
  // graph in memory, and the next request fails with "Cannot find module './415.js'" —
  // which tells the reader nothing about what happened.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // Standalone output only when the Docker build asks for it. It is the right shape for
  // a container — a self-contained server without node_modules — but `next start` refuses
  // to serve it, so making it unconditional means every local production run silently
  // serves the wrong thing.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // typedRoutes is off: every route here is built from a runtime organization id and
  // query parameters, so it would require casting almost every href to Route — which
  // removes the checking it exists to provide.
  typedRoutes: false,
  // The end-to-end suite drives the dev server over 127.0.0.1 while it serves assets
  // from localhost; without this Next warns on every request.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  images: {
    // Competitor favicons come from arbitrary domains, so remote images are proxied
    // through Next's optimiser rather than embedded directly.
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "geolocation=(), microphone=(), camera=()",
          },
        ],
      },
    ];
  },
};

export default config;
