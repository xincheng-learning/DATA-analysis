import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || '.next',
  devIndicators: false,
  output: "standalone",
  // Keep local QA builds within this workstation's memory limit.
  experimental: { proxyTimeout: 120_000, cpus: 1 },
  async rewrites() {
    return process.env.NEXT_PUBLIC_API_URL === "/api" ? [{
      source: "/api/:path*",
      has: [{ type: "host", value: "localhost|127\\.0\\.0\\.1" }],
      destination: "http://127.0.0.1:8000/:path*",
    }] : [];
  },
};

export default nextConfig;
