/**
 * 通用同源代理：除专用聊天 POST 外，其余 /api/* 请求保持路径与查询参数不变。
 * 登录态只通过 Authorization 透传；前端不直接暴露 Python 服务地址。
 */
export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_BASE =
  process.env.PYTHON_BACKEND_BASE || "http://127.0.0.1:8000";

async function handler(req: Request) {
  const url = new URL(req.url);
  // 保留 path + query，状态探测和显式停止等子路由才能准确命中后端。
  const backendUrl = `${BACKEND_BASE}${url.pathname}${url.search}`;

  const authHeader = req.headers.get("authorization") || "";
  const contentType = req.headers.get("content-type") || "application/json";

  const headers: Record<string, string> = {
    ...(authHeader ? { Authorization: authHeader } : {}),
    ...(contentType ? { "Content-Type": contentType } : {}),
  };

  // For SSE streaming endpoints, request text/event-stream
  if (url.pathname.includes("/chat/stream")) {
    headers["Accept"] = "text/event-stream";
  }

  let body: BodyInit | null = null;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.text();
  }

  let response: Response;
  try {
    response = await fetch(backendUrl, {
      method: req.method,
      headers,
      body,
      signal: req.signal,
      // @ts-expect-error — Node.js fetch duplex
      duplex: body ? "half" : undefined,
    });
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: `无法连接到 Python 后端: ${err instanceof Error ? err.message : String(err)}`,
      }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  // SSE 仍采用字节透明管道，不在 Next.js 中调用 response.text()。
  const isSse = response.headers.get("content-type")?.includes("text/event-stream");
  if (isSse) {
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  }

  // Forward regular JSON responses
  const resBody = await response.text();
  return new Response(resBody, {
    status: response.status,
    headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
    },
  });
}

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;
