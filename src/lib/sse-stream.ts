/**
 * SSE 客户端的纯函数/纯状态基础设施。
 *
 * 数据依次经过：UTF-8 TextDecoder → SSEFrameParser → dispatchSSEFrames
 * → FrameDeltaBuffer → requestAnimationFrame。每一层只解决一个问题：
 * TCP 任意拆包、事件去重续传、坏帧隔离，以及高频增量合帧渲染。
 * 这里不依赖 React，因此同一套协议可以直接被 eval 脚本验证。
 */

const DEFAULT_MAX_BUFFER_CHARS = 1_000_000;

export interface ParsedSSEFrame {
  data: string;
  event?: string;
  id?: string;
  retry?: number;
  /** 流结束时从尾部残留恢复的帧；可能不完整，解析成功前不能推进游标。 */
  recovered?: boolean;
}

function findFrameBoundary(buffer: string): { index: number; length: number } | null {
  const lf = buffer.indexOf("\n\n");
  const crlf = buffer.indexOf("\r\n\r\n");
  if (lf < 0 && crlf < 0) return null;
  if (crlf >= 0 && (lf < 0 || crlf < lf)) {
    return { index: crlf, length: 4 };
  }
  return { index: lf, length: 2 };
}

function parseFrame(rawFrame: string): ParsedSSEFrame | null {
  const data: string[] = [];
  let event: string | undefined;
  let id: string | undefined;
  let retry: number | undefined;

  for (const line of rawFrame.split(/\r\n|\r|\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "data":
        data.push(value);
        break;
      case "event":
        event = value;
        break;
      case "id":
        if (!value.includes("\0")) id = value;
        break;
      case "retry": {
        const parsed = Number(value);
        if (Number.isInteger(parsed) && parsed >= 0) retry = parsed;
        break;
      }
    }
  }

  if (data.length === 0) return null;
  return { data: data.join("\n"), event, id, retry };
}

/**
 * 增量 SSE 分帧器。
 *
 * TCP chunk 与 SSE event 没有一一对应关系：一个 JSON 可能横跨多个 chunk，
 * 一个 chunk 也可能含多个 event。因此先把文本累积到 buffer，只按空行边界切帧，
 * 再解析 data/id/retry 字段；绝不能对每个网络 chunk 直接 JSON.parse。
 */
export class SSEFrameParser {
  private buffer = "";
  private readonly maxBufferChars: number;

  constructor(maxBufferChars = DEFAULT_MAX_BUFFER_CHARS) {
    this.maxBufferChars = maxBufferChars;
  }

  push(chunk: string): ParsedSSEFrame[] {
    this.buffer += chunk;
    const frames = this.drain();
    if (this.buffer.length > this.maxBufferChars) {
      throw new Error(`SSE frame exceeded ${this.maxBufferChars} buffered characters`);
    }
    return frames;
  }

  finish(): ParsedSSEFrame[] {
    const frames = this.drain();
    if (this.buffer) {
      const trailing = parseFrame(this.buffer);
      this.buffer = "";
      if (trailing) {
        trailing.recovered = true; // no blank-line terminator: may be truncated
        frames.push(trailing);
      }
    }
    return frames;
  }

  private drain(): ParsedSSEFrame[] {
    const frames: ParsedSSEFrame[] = [];
    while (true) {
      const boundary = findFrameBoundary(this.buffer);
      if (!boundary) break;
      const rawFrame = this.buffer.slice(0, boundary.index);
      this.buffer = this.buffer.slice(boundary.index + boundary.length);
      const frame = parseFrame(rawFrame);
      if (frame) frames.push(frame);
    }
    return frames;
  }
}

/**
 * 文本双指针缓冲：写指针是 chunks.length，读指针是 publishedChunkCount。
 * 网络增量只做 O(1) append；每个动画帧才把未发布区间 join 一次，避免每个 token
 * 都复制整段字符串并触发 React 渲染。
 */
export class FrameDeltaBuffer {
  private chunks: string[] = [];
  private publishedChunkCount = 0;
  private publishedValue = "";
  private pendingHighSurrogate = "";
  private totalChars = 0;
  private readonly maxChars: number;

  constructor(maxChars = Number.POSITIVE_INFINITY) {
    this.maxChars = maxChars;
  }

  /**
   * Pre-fill the published value (e.g. a persisted draft after a page
   * refresh) so subsequent deltas append seamlessly and flush() returns
   * draft + new deltas as one continuous string.
   */
  seed(value: string): void {
    this.chunks = [];
    this.publishedChunkCount = 0;
    const accepted = value.slice(0, this.maxChars);
    const { renderable, trailingHighSurrogate } = splitRenderableUnicode(
      accepted,
      false,
    );
    this.publishedValue = renderable;
    this.pendingHighSurrogate = trailingHighSurrogate;
    this.totalChars = accepted.length;
  }

  append(delta: string): void {
    if (!delta || this.totalChars >= this.maxChars) return;
    const accepted = delta.slice(0, this.maxChars - this.totalChars);
    if (!accepted) return;
    this.chunks.push(accepted);
    this.totalChars += accepted.length;
  }

  get hasPending(): boolean {
    return Boolean(this.pendingHighSurrogate)
      || this.publishedChunkCount < this.chunks.length;
  }

  /**
   * 只发布完整 Unicode 码点。上游可能把代理对拆到两个 delta；若先渲染高半区，
   * 浏览器会短暂显示 �。普通 flush 暂存尾部高代理，final flush 才丢弃真坏数据。
   */
  flush(final = false): string {
    const writePointer = this.chunks.length;
    if (this.pendingHighSurrogate || this.publishedChunkCount < writePointer) {
      const unpublished = this.pendingHighSurrogate + this.chunks
        .slice(this.publishedChunkCount, writePointer)
        .join("");
      const { renderable, trailingHighSurrogate } = splitRenderableUnicode(
        unpublished,
        final,
      );
      this.publishedValue += renderable;
      this.pendingHighSurrogate = trailingHighSurrogate;
      this.publishedChunkCount = writePointer;

      // All chunks have crossed the read pointer; compact without changing the
      // published string so long sessions do not retain thousands of entries.
      if (this.publishedChunkCount >= 1_024) {
        this.chunks = [];
        this.publishedChunkCount = 0;
      }
    }
    return this.publishedValue;
  }
}

function splitRenderableUnicode(
  value: string,
  final: boolean,
): { renderable: string; trailingHighSurrogate: string } {
  let renderable = "";
  let trailingHighSurrogate = "";

  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        renderable += value[index] + value[index + 1];
        index += 1;
      } else if (!final && index === value.length - 1) {
        trailingHighSurrogate = value[index];
      }
      // A non-trailing high surrogate is malformed and deliberately omitted.
    } else if (code < 0xdc00 || code > 0xdfff) {
      renderable += value[index];
    }
    // A low surrogate without its high half is malformed and omitted too.
  }

  return { renderable, trailingHighSurrogate };
}

// ============================================================
// Fault-tolerant frame payload parsing
// ============================================================

export type FrameDataParseResult =
  | { ok: true; value: unknown }
  | { ok: false; error: Error };

/**
 * Parse one frame's `data:` payload as JSON without ever throwing.
 *
 * A single corrupt frame (truncated JSON, proxy injection, encoding damage)
 * must not kill the whole stream: the caller skips the bad frame while the
 * event cursor still advances past it, so a resume will not replay the poison
 * frame into an infinite retry loop.
 */
export function safeParseFrameData(frame: ParsedSSEFrame): FrameDataParseResult {
  try {
    return { ok: true, value: JSON.parse(frame.data) };
  } catch (cause) {
    return {
      ok: false,
      error: cause instanceof Error ? cause : new Error(String(cause)),
    };
  }
}

// ============================================================
// Shared frame dispatch policy
// ============================================================

export interface SSECursor {
  lastEventId: string;
  retryMs?: number;
}

export interface FrameDispatchOutcome {
  /** True once a terminal event (done/error) was dispatched. */
  terminal: boolean;
  /** Frames skipped due to unparseable payloads or handler failures. */
  dropped: number;
}

function isAlreadyAppliedEventId(eventId: string, lastEventId: string): boolean {
  if (!lastEventId) return false;

  // The backend journal uses monotonically increasing decimal IDs. Comparing
  // them here makes overlapping subscribers and Last-Event-ID replay
  // idempotent. Keep exact-match protection for non-numeric SSE servers.
  if (/^\d+$/.test(eventId) && /^\d+$/.test(lastEventId)) {
    return Number(eventId) <= Number(lastEventId);
  }
  return eventId === lastEventId;
}

/**
 * 解析后事件的唯一分发策略：
 *
 * - 有完整空行结尾的坏帧先推进游标再丢弃，避免重连永远重放同一“毒帧”；
 * - 无结尾的 recovered 帧可能被截断，只有解析成功才推进游标，失败则等待重放；
 * - 小于等于共享游标的事件直接忽略，Strict Mode/HMR 短暂双订阅也不会重复追加；
 * - 单帧 JSON 或处理器错误只影响该帧，不终止整条流。
 */
export function dispatchSSEFrames<T extends { type?: string }>(
  frames: ParsedSSEFrame[],
  cursor: SSECursor,
  handle: (event: T) => void,
  isTerminal: (event: T) => boolean,
  onDrop?: (frame: ParsedSSEFrame, cause: unknown) => void,
): FrameDispatchOutcome {
  let terminal = false;
  let dropped = 0;

  for (const frame of frames) {
    if (
      frame.id !== undefined
      && isAlreadyAppliedEventId(frame.id, cursor.lastEventId)
    ) {
      continue;
    }
    if (!frame.recovered) {
      if (frame.id !== undefined) cursor.lastEventId = frame.id;
      if (frame.retry !== undefined) cursor.retryMs = frame.retry;
    }
    if (!frame.data) continue;

    const parsed = safeParseFrameData(frame);
    if (!parsed.ok) {
      dropped += 1;
      onDrop?.(frame, parsed.error);
      continue;
    }
    if (frame.recovered) {
      if (frame.id !== undefined) cursor.lastEventId = frame.id;
      if (frame.retry !== undefined) cursor.retryMs = frame.retry;
    }

    const event = parsed.value as T;
    try {
      handle(event);
    } catch (cause) {
      dropped += 1;
      onDrop?.(frame, cause);
    }
    if (isTerminal(event)) terminal = true;
  }

  return { terminal, dropped };
}

// ============================================================
// Stall watchdog (断流检测)
// ============================================================

/**
 * 半开连接看门狗。网络断掉但没有 TCP FIN 时 reader.read() 可能永久 pending；
 * 后端定期发送 keepalive，所以长时间收不到任何字节即可判定断流。超时后取消
 * reader，让上层统一进入 Last-Event-ID 重连，不再维护另一套特殊恢复逻辑。
 */
export class StallWatchdog {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fired = false;
  private readonly timeoutMs: number;
  private readonly onStall: () => void;

  constructor(timeoutMs: number, onStall: () => void) {
    this.timeoutMs = timeoutMs;
    this.onStall = onStall;
  }

  /** (Re)start the countdown. Call on connect and on every received chunk. */
  ping(): void {
    this.fired = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.fired = true;
      this.onStall();
    }, this.timeoutMs);
  }

  /** Stop the countdown (terminal event, user stop, or stream end). */
  disarm(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  /** True after the stall callback fired (for diagnostics/tests). */
  get hasFired(): boolean {
    return this.fired;
  }
}

// ============================================================
// Paint scheduler (rAF + timeout 混合调度)
// ============================================================

export interface PaintScheduler {
  /** Queue one commit; coalesces any number of calls into a single paint. */
  schedule(): void;
  /** Drop a queued commit (stream reset / finalize / unmount). */
  cancel(): void;
  /** True while a commit is queued (diagnostics/tests). */
  readonly pending: boolean;
}

/**
 * 每个浏览器绘制帧最多提交一次 React 状态。
 *
 * 隐藏标签页会暂停 rAF，因此同时安排一个低频 timeout，二者谁先触发谁提交并
 * 清理另一方。前台与绘制同步，后台也不会积累成返回页面时的一次巨型卡顿。
 */
export function createPaintScheduler(
  commit: () => void,
  fallbackMs = 250,
): PaintScheduler {
  let rafId: number | null = null;
  let timerId: ReturnType<typeof setTimeout> | null = null;

  const clearHandles = () => {
    if (rafId !== null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(rafId);
    }
    rafId = null;
    if (timerId !== null) clearTimeout(timerId);
    timerId = null;
  };

  const run = () => {
    clearHandles();
    commit();
  };

  return {
    schedule() {
      if (rafId !== null || timerId !== null) return; // already coalesced
      if (typeof requestAnimationFrame === "function") {
        rafId = requestAnimationFrame(run);
      }
      timerId = setTimeout(run, fallbackMs);
    },
    cancel() {
      clearHandles();
    },
    get pending() {
      return rafId !== null || timerId !== null;
    },
  };
}
