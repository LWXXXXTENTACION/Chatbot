import { limitArtifactContent } from "./artifact-security.ts";
import { extractArtifactFields } from "./partial-json.ts";
import type {
  Artifact,
  ArtifactKind,
  ChatUIMessage,
  MessagePart,
  ToolPartBase,
} from "./types.ts";

/**
 * Artifact 恢复层：实时生成时侧边栏由 SSE tool_call 增量驱动；刷新或重新进入
 * 对话时，再从数据库保存的 tool message 重建同一个 Artifact。两条路径共用这里
 * 的字段归一化和长度限制，避免“生成时能看、重进后消失”。
 */

const ARTIFACT_KINDS = new Set<ArtifactKind>([
  "code",
  "html",
  "markdown",
  "svg",
]);

function artifactKind(value: unknown): ArtifactKind {
  return typeof value === "string" && ARTIFACT_KINDS.has(value as ArtifactKind)
    ? value as ArtifactKind
    : "code";
}

/** 同时兼容流式阶段的半截 JSON 字符串和持久化后的完整工具参数对象。 */
export function artifactFromMessagePart(part: MessagePart): Artifact | null {
  if (part.type !== "tool-create_artifact") return null;
  const toolPart = part as ToolPartBase;
  const rawInput = toolPart.input;
  const input = typeof rawInput === "string"
    ? extractArtifactFields(rawInput)
    : rawInput && typeof rawInput === "object"
      ? rawInput as Record<string, unknown>
      : null;
  if (!input || !toolPart.toolCallId) return null;
  if (
    typeof input.title !== "string"
    && typeof input.kind !== "string"
    && typeof input.language !== "string"
    && typeof input.content !== "string"
  ) {
    return null;
  }

  return {
    id: toolPart.toolCallId,
    title: typeof input.title === "string" && input.title
      ? input.title
      : "未命名工件",
    kind: artifactKind(input.kind),
    language: typeof input.language === "string" ? input.language : undefined,
    content: limitArtifactContent(
      typeof input.content === "string" ? input.content : "",
    ),
    streaming: toolPart.state === "input-streaming",
  };
}

/** 一个对话可能产生多个工件，重新进入时默认恢复消息顺序中最新的一个。 */
export function latestArtifactFromMessages(
  messages: ChatUIMessage[],
): Artifact | null {
  let latest: Artifact | null = null;
  for (const message of messages) {
    for (const part of message.parts) {
      latest = artifactFromMessagePart(part) ?? latest;
    }
  }
  return latest;
}
