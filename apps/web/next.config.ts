import type { NextConfig } from "next";

// The browser only ever talks to this Next.js server; /api/v1/* is proxied to FastAPI
// server-side, so provider credentials and the backend URL never reach the client (ADR-010).
const apiUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiUrl}/api/v1/:path*` }];
  },
};

export default nextConfig;
