import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "同人互动沙盒控制台",
  description: "面向中文叙事玩家的多面板互动世界引擎。"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="app-body">{children}</body>
    </html>
  );
}
