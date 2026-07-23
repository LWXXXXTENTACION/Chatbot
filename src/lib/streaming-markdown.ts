/**
 * 流式 Markdown 稳定投影。
 *
 * 普通文本可以立刻交给 Markdown 渲染器，保持逐帧增长；真正会引发布局突变的
 * “未闭合尾部”暂时截住，例如代码围栏、表格表头/半行、链接和强调语法。
 * 等结束符到达后，下一帧会自动返回完整正文，ReactMarkdown 再生成正式结构。
 */

export type PendingMarkdownKind =
  | "code-block"
  | "table"
  | "table-row"
  | "link"
  | "inline-code"
  | "emphasis"
  | "marker";

export interface StreamingMarkdownProjection {
  visibleText: string;
  pendingKind: PendingMarkdownKind | null;
}

interface MarkdownLine {
  content: string;
  start: number;
  end: number;
  terminated: boolean;
}

interface PendingCandidate {
  start: number;
  kind: PendingMarkdownKind;
}

interface FenceScan {
  maskedText: string;
  pendingStart: number | null;
}

interface InlineCodeScan {
  maskedText: string;
  pendingStart: number | null;
}

function markdownLines(value: string): MarkdownLine[] {
  if (!value) return [];
  const lines: MarkdownLine[] = [];
  let start = 0;
  while (start < value.length) {
    const newline = value.indexOf("\n", start);
    if (newline < 0) {
      lines.push({
        content: value.slice(start),
        start,
        end: value.length,
        terminated: false,
      });
      return lines;
    }
    lines.push({
      content: value.slice(start, newline),
      start,
      end: newline + 1,
      terminated: true,
    });
    start = newline + 1;
  }
  // 保留末尾空行，才能区分“表头刚换行”和“表格正文已经完整换行”。
  lines.push({
    content: "",
    start: value.length,
    end: value.length,
    terminated: false,
  });
  return lines;
}

function maskRanges(value: string, ranges: Array<[number, number]>): string {
  if (ranges.length === 0) return value;
  const characters = value.split("");
  for (const [start, end] of ranges) {
    for (let index = start; index < end; index += 1) {
      if (characters[index] !== "\n" && characters[index] !== "\r") {
        characters[index] = " ";
      }
    }
  }
  return characters.join("");
}

function fenceMarker(line: string): { character: "`" | "~"; length: number } | null {
  const match = /^[ \t]{0,3}(`{3,}|~{3,})/.exec(line);
  if (!match) return null;
  return {
    character: match[1][0] as "`" | "~",
    length: match[1].length,
  };
}

function isClosingFence(
  line: string,
  open: { character: "`" | "~"; length: number },
): boolean {
  const marker = fenceMarker(line);
  if (!marker || marker.character !== open.character || marker.length < open.length) {
    return false;
  }
  const remainder = line.trimStart().slice(marker.length);
  return remainder.trim().length === 0;
}

function scanFencedBlocks(value: string): FenceScan {
  const ranges: Array<[number, number]> = [];
  let open: {
    character: "`" | "~";
    length: number;
    start: number;
  } | null = null;

  for (const line of markdownLines(value)) {
    if (open) {
      if (isClosingFence(line.content, open)) {
        ranges.push([open.start, line.end]);
        open = null;
      }
      continue;
    }
    const marker = fenceMarker(line.content);
    if (marker) open = { ...marker, start: line.start };
  }

  return {
    maskedText: maskRanges(value, ranges),
    pendingStart: open?.start ?? null,
  };
}

function tableCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  if (!line.includes("|")) return false;
  const cells = tableCells(line);
  return cells.length >= 2
    && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isPartialTableSeparator(line: string): boolean {
  return line.includes("|")
    && line.includes("-")
    && /^[\s|:-]+$/.test(line)
    && !isTableSeparator(line);
}

function isPotentialTableRow(line: string): boolean {
  if (!line.includes("|") || /^[\s|:-]+$/.test(line)) return false;
  return tableCells(line).length >= 2;
}

function findPendingTable(value: string): PendingCandidate | null {
  const lines = markdownLines(value);
  if (lines.length === 0) return null;

  let activeSeparatorIndex = -1;
  for (let index = 0; index + 1 < lines.length; index += 1) {
    if (
      isPotentialTableRow(lines[index].content)
      && isTableSeparator(lines[index + 1].content)
    ) {
      activeSeparatorIndex = index + 1;
    }
    if (
      activeSeparatorIndex >= 0
      && index > activeSeparatorIndex
      && lines[index].content.trim() === ""
    ) {
      activeSeparatorIndex = -1;
    }
  }

  const lastIndex = lines.length - 1;
  const last = lines[lastIndex];
  const previous = lines[lastIndex - 1];

  // 表头已经换行，但分隔行尚未到达：先保留一个表格占位。
  if (
    last.content.trim() === ""
    && previous
    && isPotentialTableRow(previous.content)
    && activeSeparatorIndex < 0
  ) {
    return { start: previous.start, kind: "table" };
  }

  // 正在接收 `| --- | --- |`，表头和分隔符必须一起交给 GFM。
  if (
    previous
    && isPotentialTableRow(previous.content)
    && isPartialTableSeparator(last.content)
  ) {
    return { start: previous.start, kind: "table" };
  }

  // 表格结构已经成立时，只暂存尚未换行的最后一行，已完成行继续显示。
  if (
    activeSeparatorIndex >= 0
    && lastIndex > activeSeparatorIndex
    && !last.terminated
    && last.content.includes("|")
  ) {
    return { start: last.start, kind: "table-row" };
  }

  // 当前行可能成为表头；只有收到下一行后才能确定它是不是 GFM 表格。
  if (
    activeSeparatorIndex < 0
    && !last.terminated
    && isPotentialTableRow(last.content)
  ) {
    return { start: last.start, kind: "table" };
  }

  return null;
}

function isEscaped(value: string, index: number): boolean {
  let slashes = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) {
    slashes += 1;
  }
  return slashes % 2 === 1;
}

function scanInlineCode(value: string): InlineCodeScan {
  const ranges: Array<[number, number]> = [];
  let open: { start: number; length: number } | null = null;
  let index = 0;

  while (index < value.length) {
    if (value[index] !== "`" || isEscaped(value, index)) {
      index += 1;
      continue;
    }
    let end = index + 1;
    while (value[end] === "`") end += 1;
    const length = end - index;
    if (length >= 3) {
      index = end;
      continue;
    }
    if (!open) {
      open = { start: index, length };
    } else if (open.length === length) {
      ranges.push([open.start, end]);
      open = null;
    }
    index = end;
  }

  return {
    maskedText: maskRanges(value, ranges),
    pendingStart: open?.start ?? null,
  };
}

function findUnclosedDelimiter(value: string, delimiter: string): number | null {
  let open = -1;
  let cursor = 0;
  while (cursor < value.length) {
    const found = value.indexOf(delimiter, cursor);
    if (found < 0) break;
    cursor = found + delimiter.length;
    if (isEscaped(value, found)) continue;
    open = open < 0 ? found : -1;
  }
  return open < 0 ? null : open;
}

function findPendingLink(value: string): number | null {
  const citationStart = value.lastIndexOf("[[cite:");
  if (citationStart >= 0 && value.indexOf("]]", citationStart + 7) < 0) {
    return citationStart;
  }

  const destinationStart = value.lastIndexOf("](");
  if (
    destinationStart >= 0
    && value.indexOf(")", destinationStart + 2) < 0
  ) {
    const labelStart = value.lastIndexOf("[", destinationStart);
    if (labelStart >= 0) {
      return labelStart > 0 && value[labelStart - 1] === "!"
        ? labelStart - 1
        : labelStart;
    }
  }

  const autoLinkStart = Math.max(
    value.lastIndexOf("<http://"),
    value.lastIndexOf("<https://"),
  );
  if (autoLinkStart >= 0 && value.indexOf(">", autoLinkStart + 1) < 0) {
    return autoLinkStart;
  }
  return null;
}

function findMarkerOnlyTail(value: string): number | null {
  const lines = markdownLines(value);
  const last = lines.at(-1);
  if (!last || last.terminated) return null;
  const trimmed = last.content.trim();
  if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) return null;
  return /^[ \t]{0,3}(?:#{1,6}|>|[-+*]|\d+[.)]|[-*_]{2})[ \t]*$/.test(
    last.content,
  )
    ? last.start
    : null;
}

export function projectStreamingMarkdown(
  source: string,
): StreamingMarkdownProjection {
  if (!source) return { visibleText: "", pendingKind: null };

  const fence = scanFencedBlocks(source);
  if (fence.pendingStart !== null) {
    return {
      visibleText: source.slice(0, fence.pendingStart).trimEnd(),
      pendingKind: "code-block",
    };
  }

  const candidates: PendingCandidate[] = [];
  const table = findPendingTable(fence.maskedText);
  if (table) candidates.push(table);

  const inlineCode = scanInlineCode(fence.maskedText);
  if (inlineCode.pendingStart !== null) {
    candidates.push({ start: inlineCode.pendingStart, kind: "inline-code" });
  }

  const linkStart = findPendingLink(inlineCode.maskedText);
  if (linkStart !== null) {
    candidates.push({ start: linkStart, kind: "link" });
  }

  for (const delimiter of ["**", "__", "~~"]) {
    const start = findUnclosedDelimiter(inlineCode.maskedText, delimiter);
    if (start !== null) candidates.push({ start, kind: "emphasis" });
  }

  const markerStart = findMarkerOnlyTail(inlineCode.maskedText);
  if (markerStart !== null) {
    candidates.push({ start: markerStart, kind: "marker" });
  }

  if (candidates.length === 0) {
    return { visibleText: source, pendingKind: null };
  }
  const pending = candidates.reduce((earliest, candidate) =>
    candidate.start < earliest.start ? candidate : earliest,
  );
  return {
    visibleText: source.slice(0, pending.start).trimEnd(),
    pendingKind: pending.kind,
  };
}
