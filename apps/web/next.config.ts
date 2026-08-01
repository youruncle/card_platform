import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    const apiUrl =
      process.env.API_INTERNAL_URL ??
      (process.env.NODE_ENV === "development" ? "http://127.0.0.1:8010" : undefined);
    return apiUrl
      ? [
          {
            source: "/v1/:path*",
            destination: `${apiUrl}/v1/:path*`,
          },
        ]
      : [];
  },
};

export default nextConfig;
