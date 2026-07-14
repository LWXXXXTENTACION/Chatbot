"use client";

import {
  isValidElement,
  memo,
  type ComponentPropsWithoutRef,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { CodeBlock } from "./CodeBlock";
import type { Source } from "@/lib/types";

/** Recursively collect raw text from a hast node (for the copy button). */
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
    const codeProps = children.props as {
      className?: string;
      node?: unknown;
    };
    const match = /language-([\w-]+)/.exec(codeProps.className ?? "");
    language = match?.[1];
    raw = hastToText(codeProps.node);
  }

  return (
    <CodeBlock language={language} raw={raw}>
      {children}
    </CodeBlock>
  );
}

function MarkdownImpl({
  children,
  sources,
}: {
  children: string;
  sources?: Source[];
}) {
  // Pre-process: replace [1], [2] etc. with markdown citation links.
  // Only transform when sources are available and the number has a match.
  const processedText =
    sources?.length
      ? children.replace(/\[(\d+)\]/g, (_match, n) => {
          const idx = parseInt(n) - 1;
          if (idx >= 0 && idx < sources.length && sources[idx]?.url) {
            return `[${n}](__citation__:${idx})`;
          }
          return _match; // keep as-is if no matching source
        })
      : children;

  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
        ]}
        components={{
          pre: Pre,
          a: (props) => {
            const href = props.href || "";
            // Custom citation links — render as superscript badges
            if (href.startsWith("__citation__:")) {
              const idx = parseInt(href.split(":")[1]);
              const source = sources?.[idx];
              return (
                <sup className="citation-badge">
                  <a
                    href={source?.url || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={source?.title || ""}
                  >
                    [{idx + 1}]
                  </a>
                </sup>
              );
            }
            return (
              <a {...props} target="_blank" rel="noopener noreferrer" />
            );
          },
        }}
      >
        {processedText}
      </ReactMarkdown>
    </div>
  );
}

// Memoized so streaming re-renders only reparse when text actually changes.
export const Markdown = memo(MarkdownImpl, (a, b) => {
  if (a.children !== b.children) return false;
  const aLen = a.sources?.length ?? 0;
  const bLen = b.sources?.length ?? 0;
  if (aLen !== bLen) return false;
  return JSON.stringify(a.sources) === JSON.stringify(b.sources);
});
