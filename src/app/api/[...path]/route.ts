/**
 * Catch-all proxy: forwards all /api/* requests to the Python LangGraph backend.
 *
 * Each request is forwarded to http://127.0.0.1:8000 with the same path and body.
 * Authorization headers are forwarded for authenticated endpoints.
 */
export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_BASE =
  process.env.PYTHON_BACKEND_BASE || "http://127.0.0.1:8000";

async function handler(req: Request) {
  const url = new URL(req.url);
  // Reconstruct the backend URL with the same path + query
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

  // Forward SSE streams
  const isSse = response.headers.get("content-type")?.includes("text/event-stream");
  if (isSse) {
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
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
