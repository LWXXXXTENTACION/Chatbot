"use client";

import {
  isValidElement,
  memo,
  useMemo,
  useId,
  type ComponentPropsWithoutRef,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { CodeBlock } from "./CodeBlock";
import type { Source } from "@/lib/types";
import {
  projectStreamingMarkdown,
  type PendingMarkdownKind,
} from "@/lib/streaming-markdown";

/**
 * Markdown 采用“普通文本逐帧、未闭合结构占位”的策略：打字机效果不会丢失，
 * 代码围栏、表格、链接等半成品也不会以 Markdown 源码形式短暂裸露。
 */

/** 递归提取 hast 文本，供代码块复制按钮使用。 */
function hastToText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const n = node as { type?: string; value?: string; children?: unknown[] };
  if (n.type === "text") return n.value ?? "";
  if (Array.isArray(n.children)) return n.children.map(hastToText).join("");
  return "";
}

function Pre({ children }: ComponentPropsWithoutRef<"pre">) {
  let language: string | undefined;
  let raw = "";

  if (isValidElement(children)) {
    const codeProps = children.props as { className?: string; node?: unknown };
    language = /language-([\w-]+)/.exec(codeProps.className ?? "")?.[1];
    raw = hastToText(codeProps.node);
  }

  return <CodeBlock language={language} raw={raw}>{children}</CodeBlock>;
}

function safeUrl(source?: Source): string | null {
  if (!source?.url) return null;
  try {
    const url = new URL(source.url);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function sourceHost(source: Source): string {
  try {
    return new URL(source.url).hostname.replace(/^www\./, "");
  } catch {
    return source.title || "来源";
  }
}

function citationMarkdown(text: string, sources: Source[]): string {
  // Preferred model protocol: [[cite:1]] or [[cite:1,2]].
  const explicit = text.replace(/\[\[cite:([\d,\s]+)\]\]/gi, (marker, raw) => {
    const indexes = String(raw)
      .split(",")
      .map((value) => Number.parseInt(value.trim(), 10) - 1)
      .filter((index, position, all) =>
        index >= 0 &&
        index < sources.length &&
        Boolean(safeUrl(sources[index])) &&
        all.indexOf(index) === position,
      );
    return indexes.length ? `[来源](#__citation__:${indexes.join(",")})` : marker;
  });

  // Keep old conversations using [1] compatible, without rewriting links.
  return explicit.replace(/\[(\d+)\](?!\s*\()/g, (marker, number) => {
    const index = Number.parseInt(number, 10) - 1;
    return index >= 0 && index < sources.length && safeUrl(sources[index])
      ? `[来源](#__citation__:${index})`
      : marker;
  });
}

function CitationItem({ index, source }: { index: number; source: Source }) {
  const descriptionId = useId();
  const href = safeUrl(source);
  if (!href) return null;

  return (
    <span className="citation-option-wrap">
      <a
        className="citation-option-link"
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`来源 ${index + 1}：${source.title}`}
      >
        <span
          data-index={index + 1}
          className="citation-option options-item-Yv7oFR"
          aria-describedby={descriptionId}
        >
          {index + 1}
        </span>
      </a>
      <span id={descriptionId} className="citation-tooltip" role="tooltip">
        <strong>{source.title || `来源 ${index + 1}`}</strong>
        <span>{sourceHost(source)}</span>
      </span>
    </span>
  );
}

function SourceSpanLink({ indexes, sources }: { indexes: number[]; sources: Source[] }) {
  return (
    <span className="source-span-link" aria-label="引用来源">
      {indexes.map((index) => {
        const source = sources[index];
        return source ? (
          <CitationItem key={`${index}-${source.url}`} index={index} source={source} />
        ) : null;
      })}
    </span>
  );
}

function sameSources(a?: Source[], b?: Source[]): boolean {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  return a.every((source, index) =>
    source.url === b[index].url &&
    source.title === b[index].title &&
    source.content === b[index].content,
  );
}

const MarkdownDocument = memo(
  function MarkdownDocument({
    text,
    sources,
    highlight,
  }: {
    text: string;
    sources: Source[];
    highlight: boolean;
  }) {
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={
          highlight
            ? [[rehypeHighlight, { detect: true, ignoreMissing: true }]]
            : []
        }
        components={{
          pre: Pre,
          a: (props) => {
            const href = props.href || "";
            if (href.startsWith("#__citation__:")) {
              const indexes = href
                .slice("#__citation__:".length)
                .split(",")
                .map((value) => Number.parseInt(value, 10))
                .filter(Number.isInteger);
              return <SourceSpanLink indexes={indexes} sources={sources} />;
            }
            return <a {...props} target="_blank" rel="noopener noreferrer" />;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    );
  },
  (previous, next) =>
    previous.text === next.text
    && previous.highlight === next.highlight
    && sameSources(previous.sources, next.sources),
);

function MarkdownImpl({
  children,
  sources = [],
  streaming = false,
}: {
  children: string;
  sources?: Source[];
  streaming?: boolean;
}) {
  return (
    <div className={`markdown ${streaming ? "markdown-streaming" : ""}`}>
      {streaming ? (
        <StreamingMarkdown text={children} sources={sources} />
      ) : (
        <CompletedMarkdown text={children} sources={sources} />
      )}
    </div>
  );
}

const PENDING_LABELS: Record<PendingMarkdownKind, string> = {
  "code-block": "正在接收代码块",
  table: "正在接收表格结构",
  "table-row": "正在接收表格行",
  link: "正在接收链接",
  "inline-code": "正在接收行内代码",
  emphasis: "正在接收格式化文本",
  marker: "正在接收 Markdown 结构",
};

function StreamingMarkdownPlaceholder({
  kind,
}: {
  kind: PendingMarkdownKind;
}) {
  // 占位符只对应当前未闭合结构；已经稳定的普通正文仍在上方逐帧增长。
  return (
    <div
      className="streaming-markdown-pending"
      data-streaming-markdown-placeholder
      role="status"
      aria-label={PENDING_LABELS[kind]}
    >
      <span className="sr-only">{PENDING_LABELS[kind]}</span>
      <span className="streaming-markdown-pending-line" />
      <span className="streaming-markdown-pending-line" />
    </div>
  );
}

function StreamingMarkdown({ text, sources }: { text: string; sources: Source[] }) {
  const projection = useMemo(() => projectStreamingMarkdown(text), [text]);
  const processedText = useMemo(
    () => sources.length
      ? citationMarkdown(projection.visibleText, sources)
      : projection.visibleText,
    [projection.visibleText, sources],
  );

  return (
    <>
      {processedText ? (
        <MarkdownDocument text={processedText} sources={sources} highlight={false} />
      ) : null}
      {projection.pendingKind ? (
        <StreamingMarkdownPlaceholder kind={projection.pendingKind} />
      ) : null}
      <span className="streaming-markdown-caret" aria-hidden="true" />
    </>
  );
}

function CompletedMarkdown({ text, sources }: { text: string; sources: Source[] }) {
  const processedText = useMemo(
    () => sources.length ? citationMarkdown(text, sources) : text,
    [text, sources],
  );
  return <MarkdownDocument text={processedText} sources={sources} highlight />;
}

export const Markdown = memo(
  MarkdownImpl,
  (previous, next) => {
    return previous.children === next.children
      && previous.streaming === next.streaming
      && sameSources(previous.sources, next.sources);
  },
);
