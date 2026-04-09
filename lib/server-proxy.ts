const ENGINE_API_BASE_URL = process.env.ENGINE_API_BASE_URL;

export async function proxyToEngine(
  request: Request,
  path: string
): Promise<Response> {
  if (!ENGINE_API_BASE_URL) {
    return Response.json(
      {
        error: "未配置 ENGINE_API_BASE_URL，前端代理无法连接后端引擎。"
      },
      { status: 503 }
    );
  }

  const upstreamUrl = new URL(path, ENGINE_API_BASE_URL).toString();
  const body = await request.text();
  const response = await fetch(upstreamUrl, {
    method: request.method,
    headers: {
      "Content-Type": request.headers.get("Content-Type") ?? "application/json"
    },
    body,
    cache: "no-store"
  });

  const contentType =
    response.headers.get("Content-Type") ?? "application/json";
  const payload = await response.text();

  return new Response(payload, {
    status: response.status,
    headers: {
      "Content-Type": contentType
    }
  });
}
