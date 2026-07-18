import {
  FrameDeltaBuffer,
  SSEFrameParser,
  StallWatchdog,
  createPaintScheduler,
  dispatchSSEFrames,
} from "./sse-stream.ts";
import {
  createSSESessionStore,
  type StorageLike,
} from "./sse-session.ts";
import { latestArtifactFromMessages } from "./artifacts.ts";
import type { ChatUIMessage, TextPart } from "./types.ts";

/**
 * 可重复的 SSE 回归与性能基准。基准不复制生产算法，而是直接导入生产 Parser、
 * Buffer、Dispatcher、SessionStore 和 Artifact 恢复函数，避免“测试实现通过、
 * 实际实现退化”。结果同时覆盖性能对比和协议正确性门槛。
 */

export interface SSEPerformanceEvalResult {
  generatedAt: string;
  workload: {
    deltas: number;
    characters: number;
    deltasPerFrame: number;
  };
  legacy: {
    pipelineMs: number;
    publications: number;
  };
  optimized: {
    pipelineMs: number;
    publications: number;
  };
  comparison: {
    pipelineSpeedup: number;
    publicationReductionPct: number;
  };
  protocol: {
    parserPass: boolean;
    decodedEvents: number;
    smallestChunkBytes: number;
    resumePass: boolean;
    replayedEvents: number;
  };
  robustness: {
    corruptFramePass: boolean;
    droppedFrames: number;
    watchdogPass: boolean;
    schedulerPass: boolean;
    sessionPersistencePass: boolean;
    unicodeBufferPass: boolean;
    streamingTextIntegrityPass: boolean;
    duplicateReplayPass: boolean;
    artifactRestorePass: boolean;
  };
}

function median(values: number[]): number {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.floor(ordered.length / 2)] ?? 0;
}

function projectUISnapshot(text: string): number {
  if (!text) return 0;
  let checksum = text.length;
  const samples = Math.min(48, text.length);
  for (let index = 0; index < samples; index += 1) {
    checksum ^= text.charCodeAt(Math.floor((index * text.length) / samples));
  }
  return checksum;
}

function runLegacyPipeline(deltas: string[]): number {
  let part = { type: "text", text: "" };
  let checksum = 0;
  for (let index = 0; index < deltas.length; index += 1) {
    part = { ...part, text: part.text + deltas[index] };
    checksum ^= projectUISnapshot(part.text);
  }
  return checksum;
}

function runBufferedPipeline(deltas: string[], deltasPerFrame: number): number {
  const buffer = new FrameDeltaBuffer();
  let checksum = 0;
  for (let index = 0; index < deltas.length; index += 1) {
    buffer.append(deltas[index]);
    if ((index + 1) % deltasPerFrame === 0 || index === deltas.length - 1) {
      checksum ^= projectUISnapshot(buffer.flush());
    }
  }
  return checksum;
}

function measure(operation: () => number): number {
  const samples: number[] = [];
  operation();
  operation();
  for (let index = 0; index < 7; index += 1) {
    const startedAt = performance.now();
    operation();
    samples.push(performance.now() - startedAt);
  }
  return median(samples);
}

function evaluateParser() {
  const eventCount = 240;
  let wire = "";
  let expected = "";
  for (let eventId = 1; eventId <= eventCount; eventId += 1) {
    const delta = eventId % 2 === 0 ? "流" : "S";
    expected += delta;
    wire += [
      `id: ${eventId}`,
      "event: message",
      `data:{\"type\":\"text_delta\",`,
      `data: \"messageId\":\"eval\",\"delta\":\"${delta}\"}`,
      "",
      "",
    ].join(eventId % 3 === 0 ? "\r\n" : "\n");
  }
  wire += [
    `id: ${eventCount + 1}`,
    `data: {\"type\":\"done\",\"messageId\":\"eval\"}`,
    "",
    "",
  ].join("\r\n");

  const bytes = new TextEncoder().encode(wire);
  const decoder = new TextDecoder();
  const parser = new SSEFrameParser();
  const frames = [];
  const chunkPattern = [1, 2, 3, 5, 8, 13];
  let offset = 0;
  let patternIndex = 0;
  while (offset < bytes.length) {
    const chunkSize = chunkPattern[patternIndex % chunkPattern.length];
    const end = Math.min(offset + chunkSize, bytes.length);
    frames.push(...parser.push(decoder.decode(bytes.slice(offset, end), { stream: true })));
    offset = end;
    patternIndex += 1;
  }
  const trailing = decoder.decode();
  if (trailing) frames.push(...parser.push(trailing));
  frames.push(...parser.finish());

  const payloads = frames.map((frame) => JSON.parse(frame.data) as {
    type: string;
    delta?: string;
  });
  const actual = payloads.map((payload) => payload.delta ?? "").join("");
  const ids = frames.map((frame) => Number(frame.id));
  const parserPass = frames.length === eventCount + 1
    && actual === expected
    && ids.every((id, index) => id === index + 1)
    && payloads.at(-1)?.type === "done";

  const disconnectedAt = 137;
  const replayedFrames = frames.filter((frame) => Number(frame.id) > disconnectedAt);
  const prefix = payloads
    .slice(0, disconnectedAt)
    .map((payload) => payload.delta ?? "")
    .join("");
  const replay = replayedFrames
    .map((frame) => JSON.parse(frame.data) as { delta?: string })
    .map((payload) => payload.delta ?? "")
    .join("");

  return {
    parserPass,
    decodedEvents: frames.length,
    smallestChunkBytes: Math.min(...chunkPattern),
    resumePass: prefix + replay === expected,
    replayedEvents: replayedFrames.length,
  };
}

/**
 * 痛点3·容错: a truncated-JSON frame sits between two good frames on the wire,
 * plus a second truncated frame at the very tail without a blank-line
 * terminator. Assertions, all through the shared dispatch policy:
 *  - the parser still splits every frame (buffer + delimiter)
 *  - bad payloads are dropped without throwing or killing the stream
 *  - the resume cursor passes the mid-stream poison frame (never replayed)
 *  - the cursor does NOT pass the truncated tail, so a resume replays it whole
 */
function evaluateCorruptFrameTolerance() {
  const wire = [
    "id: 1\ndata: {\"type\":\"text_delta\",\"delta\":\"A\"}\n\n",
    "id: 2\ndata: {\"type\":\"text_delta\",\"delta\":\n\n",
    "id: 3\ndata: {\"type\":\"done\",\"messageId\":\"eval\"}\n\n",
    "id: 4\ndata: {\"type\":\"text_delta\",\"del",
  ].join("");

  const parser = new SSEFrameParser();
  const frames = [];
  const bytes = new TextEncoder().encode(wire);
  const decoder = new TextDecoder();
  let offset = 0;
  let patternIndex = 0;
  const chunkPattern = [7, 5, 11, 3, 64, 1, 2];
  while (offset < bytes.length) {
    const size = chunkPattern[patternIndex % chunkPattern.length];
    const end = Math.min(offset + size, bytes.length);
    frames.push(...parser.push(decoder.decode(bytes.slice(offset, end), { stream: true })));
    offset = end;
    patternIndex += 1;
  }
  frames.push(...parser.finish());

  const cursor = { lastEventId: "0" };
  const events: Array<{ type?: string }> = [];
  const outcome = dispatchSSEFrames<{ type?: string }>(
    frames,
    cursor,
    (event) => { events.push(event); },
    (event) => event.type === "done" || event.type === "error",
  );

  const corruptFramePass = frames.length === 4
    && frames.at(-1)?.recovered === true
    && outcome.dropped === 2
    && outcome.terminal === true
    && events.length === 2
    && events[0]?.type === "text_delta"
    && events[1]?.type === "done"
    && cursor.lastEventId === "3";

  return { corruptFramePass, droppedFrames: outcome.dropped };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 痛点2·看门狗: fires after a silent window, rearms on ping, disarms cleanly. */
async function evaluateWatchdog(): Promise<boolean> {
  const firesOnSilence = await (async () => {
    const watchdog = new StallWatchdog(15, () => undefined);
    let stalled = false;
    const armed = new StallWatchdog(15, () => { stalled = true; });
    watchdog.disarm(); // unused instance exercises the disarm path
    armed.ping();
    await sleep(45);
    return stalled && armed.hasFired;
  })();

  const pingRearmsAndDisarmSuppresses = await (async () => {
    let fired = false;
    const watchdog = new StallWatchdog(20, () => { fired = true; });
    watchdog.ping();
    const keepAlive = setInterval(() => watchdog.ping(), 8);
    await sleep(50); // ~6 rearms, longer than the 20ms timeout
    clearInterval(keepAlive);
    watchdog.disarm();
    await sleep(40); // would have fired if disarm failed
    return !fired && !watchdog.hasFired;
  })();

  return firesOnSilence && pingRearmsAndDisarmSuppresses;
}

/** 痛点1·调度: N schedules coalesce into one commit; cancel drops the commit. */
async function evaluatePaintScheduler(): Promise<boolean> {
  const coalesces = await (async () => {
    let commits = 0;
    const scheduler = createPaintScheduler(() => { commits += 1; }, 15);
    scheduler.schedule();
    scheduler.schedule();
    scheduler.schedule();
    await sleep(50);
    return commits === 1;
  })();

  const cancelDrops = await (async () => {
    let commits = 0;
    const scheduler = createPaintScheduler(() => { commits += 1; }, 15);
    scheduler.schedule();
    scheduler.cancel();
    await sleep(50);
    return commits === 0 && !scheduler.pending;
  })();

  return coalesces && cancelDrops;
}

/**
 * 刷新续传: save session + draft, simulate a page refresh by building a NEW
 * store over the same storage, then assert the resume state round-trips and
 * a seeded delta buffer continues the draft text seamlessly.
 */
function evaluateSessionPersistence(): boolean {
  const map = new Map<string, string>();
  const storage: StorageLike = {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
  };
  const conversationId = "conv-eval";

  const writer = createSSESessionStore(storage);
  writer.saveSession(conversationId, {
    streamId: "stream-eval-123",
    requestBody: "{\"stream_id\":\"stream-eval-123\"}",
    lastEventId: "",
    startedAt: 1_700_000_000_000,
  });
  writer.updateCursor(conversationId, "42", 1200);
  writer.saveDraft(conversationId, {
    messageId: "msg-eval",
    parts: [
      { type: "reasoning", text: "先想", state: "streaming" },
      { type: "text", text: "答案的前半部分" },
    ],
    updatedAt: 1_700_000_001_000,
  });

  // --- simulated refresh: new store instance, same underlying storage ---
  const reader = createSSESessionStore(storage);
  const session = reader.loadSession(conversationId);
  const draft = reader.loadDraft(conversationId);

  const sessionOk = session?.streamId === "stream-eval-123"
    && session.lastEventId === "42"
    && session.retryMs === 1200
    && session.requestBody.includes("stream-eval-123");
  const draftOk = draft?.messageId === "msg-eval" && draft.parts.length === 2;

  // Resume continuity: seed from the draft, then append post-resume deltas.
  const textPart = draft?.parts.find((p): p is TextPart => p.type === "text");
  const buffer = new FrameDeltaBuffer();
  buffer.seed(textPart?.text ?? "");
  buffer.append("，后半部分");
  const continued = buffer.flush();

  reader.clear(conversationId);
  const cleared = reader.loadSession(conversationId) === null
    && reader.loadDraft(conversationId) === null;

  return sessionOk && draftOk && continued === "答案的前半部分，后半部分" && cleared;
}

/** Initial-paint integrity: never publish half of an emoji surrogate pair. */
function evaluateUnicodeBuffer(): boolean {
  const emoji = "😀";
  const buffer = new FrameDeltaBuffer();
  buffer.append(`中文${emoji[0]}`);
  const firstPaint = buffer.flush();
  const repeatedPaint = buffer.flush();
  buffer.append(`${emoji[1]}正常`);
  const secondPaint = buffer.flush();

  const malformed = new FrameDeltaBuffer();
  malformed.append(`安全文本${emoji[0]}`);
  const finalPaint = malformed.flush(true);

  return firstPaint === "中文"
    && repeatedPaint === "中文"
    && secondPaint === "中文😀正常"
    && finalPaint === "安全文本"
    && !firstPaint.includes("\uFFFD")
    && !secondPaint.includes("\uFFFD");
}

/** Streaming view receives the exact raw text; Markdown runs only at done. */
function evaluateStreamingTextIntegrity(): boolean {
  const expected = "# 标题\n\n```ts\nconst 文本 = \"你好 😀\";\n```";
  const chunks = ["# 标", "题\n\n`", "``ts\nconst 文", "本 = \"你好 ", "😀\";\n```"];
  const buffer = new FrameDeltaBuffer();
  for (const chunk of chunks) {
    buffer.append(chunk);
    buffer.flush();
  }
  return buffer.flush(true) === expected;
}

/** Multiple subscribers/replays must never append an applied event twice. */
function evaluateDuplicateReplay(): boolean {
  const cursor = { lastEventId: "0" };
  const deltas: string[] = [];
  const apply = (frames: Array<{ id: string; data: string }>) =>
    dispatchSSEFrames<{ type?: string; delta?: string }>(
      frames,
      cursor,
      (event) => {
        if (event.type === "text_delta" && event.delta) deltas.push(event.delta);
      },
      (event) => event.type === "done" || event.type === "error",
    );

  const first = [
    { id: "1", data: '{"type":"text_delta","delta":"你"}' },
    { id: "2", data: '{"type":"text_delta","delta":"好"}' },
  ];
  apply(first);
  const replayOutcome = apply([
    ...first,
    { id: "3", data: '{"type":"done","messageId":"eval"}' },
  ]);

  return deltas.join("") === "你好"
    && cursor.lastEventId === "3"
    && replayOutcome.terminal;
}

/** Persisted tool parts must be able to recreate the artifact panel. */
function evaluateArtifactRestore(): boolean {
  const message: ChatUIMessage = {
    id: "artifact-message",
    role: "assistant",
    parts: [{
      type: "tool-create_artifact",
      toolCallId: "artifact-call",
      state: "output-available",
      input: {
        title: "中文页面",
        kind: "html",
        language: "html",
        content: "<h1>你好 😀</h1>",
      },
    }],
  };
  const artifact = latestArtifactFromMessages([message]);
  return artifact?.id === "artifact-call"
    && artifact.title === "中文页面"
    && artifact.kind === "html"
    && artifact.content === "<h1>你好 😀</h1>"
    && artifact.streaming === false;
}

export async function runSSEPerformanceEval(
  deltaCount = 30_000,
  deltasPerFrame = 64,
): Promise<SSEPerformanceEvalResult> {
  const deltas = Array.from(
    { length: deltaCount },
    (_unused, index) => (index % 3 === 0 ? "终端" : "sse"),
  );
  const characters = deltas.reduce((total, delta) => total + delta.length, 0);
  const legacyMs = measure(() => runLegacyPipeline(deltas));
  const optimizedMs = measure(() => runBufferedPipeline(deltas, deltasPerFrame));
  const optimizedPublications = Math.ceil(deltaCount / deltasPerFrame);
  const protocol = evaluateParser();
  const corrupt = evaluateCorruptFrameTolerance();
  const watchdogPass = await evaluateWatchdog();
  const schedulerPass = await evaluatePaintScheduler();
  const sessionPersistencePass = evaluateSessionPersistence();
  const unicodeBufferPass = evaluateUnicodeBuffer();
  const streamingTextIntegrityPass = evaluateStreamingTextIntegrity();
  const duplicateReplayPass = evaluateDuplicateReplay();
  const artifactRestorePass = evaluateArtifactRestore();

  return {
    generatedAt: new Date().toISOString(),
    workload: { deltas: deltaCount, characters, deltasPerFrame },
    legacy: {
      pipelineMs: Number(legacyMs.toFixed(3)),
      publications: deltaCount,
    },
    optimized: {
      pipelineMs: Number(optimizedMs.toFixed(3)),
      publications: optimizedPublications,
    },
    comparison: {
      pipelineSpeedup: Number((legacyMs / Math.max(optimizedMs, 0.001)).toFixed(2)),
      publicationReductionPct: Number(
        ((1 - optimizedPublications / deltaCount) * 100).toFixed(2),
      ),
    },
    protocol,
    robustness: {
      ...corrupt,
      watchdogPass,
      schedulerPass,
      sessionPersistencePass,
      unicodeBufferPass,
      streamingTextIntegrityPass,
      duplicateReplayPass,
      artifactRestorePass,
    },
  };
}
