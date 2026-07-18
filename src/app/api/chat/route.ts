/**
 * SSE 字节透明代理：浏览器只访问同源 Next.js，Node 再连接 Python LangGraph。
 *
 * 这里不读取、拼接或重新编码 response.body，避免 UTF-8 多字节字符被代理层拆坏；
 * 浏览器端的 TextDecoder 负责流式解码。Last-Event-ID 原样转发，支持断点续传。
 */
export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_URL =
  process.env.PYTHON_BACKEND_URL || "http://127.0.0.1:8000/api/chat/stream";

export async function POST(req: Request) {
  const body = await req.text();

  // 续传游标和身份都必须原样传给 Python，代理层不维护第二份会话状态。
  const authHeader = req.headers.get("authorization") || "";
  const lastEventId = req.headers.get("last-event-id") || "";

  let response: Response;
  try {
    response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(authHeader ? { Authorization: authHeader } : {}),
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      body,
      // 浏览器切换对话时只取消这一条“订阅连接”。后端的 Graph 生产任务已和
      // HTTP 生命周期解耦，会继续写事件日志，稍后可用同一 stream_id 续订。
      signal: req.signal,
      // @ts-expect-error — Node.js fetch duplex option
      duplex: "half",
    });
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: `无法连接到 Python 后端: ${err instanceof Error ? err.message : String(err)}`,
      }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  // 直接管道转发字节流；禁用中间层缓冲，否则增量事件会积成大块才到浏览器。
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      ...(response.headers.get("x-stream-id")
        ? { "X-Stream-ID": response.headers.get("x-stream-id") as string }
        : {}),
    },
  });
}
