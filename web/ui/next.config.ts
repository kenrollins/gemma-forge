import type { NextConfig } from "next";

const FORGE_API_URL = process.env.FORGE_API_URL ?? "http://localhost:8080";

const nextConfig: NextConfig = {
  // Produces `.next/standalone/server.js` with a pruned node_modules —
  // used by the compose `forge-ui` image. Local `npm run dev` / `npm run
  // start` work unchanged.
  output: "standalone",
  allowedDevOrigins: [
    "xr7620.home.arpa",
    "10.0.100.69",
    "*.devtunnels.ms",
  ],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${FORGE_API_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
