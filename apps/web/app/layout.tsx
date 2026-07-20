import type { Metadata } from "next";
import { Inter, Space_Grotesk } from "next/font/google";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-body" });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-display" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "Card Alpha — 篮球卡投资管理",
  description: "管理篮球球星卡资产、估值与投资决策。",
  openGraph: {
    title: "Card Alpha — 篮球卡投资管理",
    description: "让每张卡，都有投资逻辑。",
    images: [{ url: "/og.png", width: 1664, height: 1008, alt: "Card Alpha" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Card Alpha — 篮球卡投资管理",
    description: "让每张卡，都有投资逻辑。",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${inter.variable} ${spaceGrotesk.variable}`}>{children}</body>
    </html>
  );
}
