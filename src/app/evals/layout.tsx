import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Eval Lab · Chatbot 优化评测",
  description: "独立的 Chatbot 版本评测、运行回放与成本质量对比系统",
};

export default function EvalsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
