import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Auth recovery plus paginated order reads can exceed the default proxy timeout.
  // Formal Canon PNGs exceed Next's default 10 MB cloned request body.
  // Keep room for the API's 80 MB whole-sheet limit and multipart framing.
  experimental: { cpus: 1, proxyTimeout: 180_000, middlewareClientMaxBodySize: "100mb" },
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
