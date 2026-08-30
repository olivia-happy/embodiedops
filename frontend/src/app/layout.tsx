import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "SignalForge", description: "基于证据的决策智能工作台" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
