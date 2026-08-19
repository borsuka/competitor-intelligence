import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Docker builds run `next start` from a standalone bundle rather than shipping
  // node_modules into the runtime image.
  output: "standalone",
  // typedRoutes is off: every route here is built from a runtime organization id and
  // query parameters, so it would require casting almost every href to Route — which
  // removes the checking it exists to provide.
  typedRoutes: false,
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
