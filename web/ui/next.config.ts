import type { NextConfig } from "next";

const FORGE_API_URL = process.env.FORGE_API_URL ?? "http://localhost:8080";

const nextConfig: NextConfig = {
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
