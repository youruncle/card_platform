import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    const publicApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
    const apiUrl =
      process.env.API_INTERNAL_URL ??
      (!publicApiUrl ? "http://127.0.0.1:8000" : undefined);
    return apiUrl
      ? [
          {
            source: "/v1/:path*",
            destination: `${apiUrl}/v1/:path*`,
          },
          {
            source: "/uploads/:path*",
            destination: `${apiUrl}/uploads/:path*`,
          },
        ]
      : [];
  },
};

export default nextConfig;
