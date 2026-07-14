/**
 * SSE proxy: forwards chat requests to the Python LangGraph backend.
 *
 * The browser connects here (port 3000), which already bypasses the system
 * proxy. Node.js then calls the Python backend on localhost (no proxy).
 * Forwards Authorization headers from the client to the backend.
 */
export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_URL =
  process.env.PYTHON_BACKEND_URL || "http://127.0.0.1:8000/api/chat/stream";

export async function POST(req: Request) {
  const body = await req.text();

  // Forward auth header from client to backend
  const authHeader = req.headers.get("authorization") || "";

  let response: Response;
  try {
    response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body,
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

  // Pipe the SSE stream directly to the client
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
