import type { Metadata } from "next";
import Script from "next/script";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthProvider } from "@/providers/AuthProvider";
import "./globals.css";

const THEME_SCRIPT = `(() => {
  let saved = null;
  try {
    saved = localStorage.getItem("chatbot.theme");
  } catch {}
  const theme = saved === "light" || saved === "dark"
    ? saved
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
})();`;

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DeepSeek Chatbot",
  description: "一个支持多种 DeepSeek 模型的 AI 聊天助手",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="h-full">
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_SCRIPT}
        </Script>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
