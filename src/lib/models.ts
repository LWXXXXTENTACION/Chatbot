export type DeepSeekModelId =
  | "deepseek-v4-flash"
  | "deepseek-v4-pro"
  | "deepseek-chat"
  | "deepseek-reasoner";

export interface DeepSeekModel {
  id: DeepSeekModelId;
  name: string;
  description: string;
  badge?: string;
  deprecated?: string;
}

export const DEEPSEEK_MODELS: DeepSeekModel[] = [
  {
    id: "deepseek-v4-flash",
    name: "DeepSeek V4 Flash",
    description: "最新一代轻量模型，响应快、成本低，适合日常对话",
    badge: "极速",
  },
  {
    id: "deepseek-v4-pro",
    name: "DeepSeek V4 Pro",
    description: "最新一代旗舰模型，能力强，适合复杂任务与推理",
    badge: "旗舰",
  },
  {
    id: "deepseek-chat",
    name: "DeepSeek V3",
    description: "上一代通用对话模型（将于 2026-07-24 停用）",
    badge: "通用",
    deprecated: "2026-07-24",
  },
  {
    id: "deepseek-reasoner",
    name: "DeepSeek R1",
    description: "上一代推理模型，会先思考再回答（将于 2026-07-24 停用）",
    badge: "推理",
    deprecated: "2026-07-24",
  },
];

export const DEFAULT_MODEL: DeepSeekModelId = "deepseek-v4-flash";

export function getModel(id: string): DeepSeekModel {
  return (
    DEEPSEEK_MODELS.find((m) => m.id === id) ?? DEEPSEEK_MODELS[0]
  );
}

export function modelSupportsTools(id: DeepSeekModelId): boolean {
  return id !== "deepseek-reasoner";
}
