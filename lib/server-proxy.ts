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

export async function proxyToEngineStream(
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
  const response = await fetch(
    upstreamUrl,
    {
      method: request.method,
      headers: {
        "Content-Type":
          request.headers.get("Content-Type") ?? "application/json",
        Accept: request.headers.get("Accept") ?? "text/event-stream"
      },
      body: request.body,
      cache: "no-store",
      duplex: "half"
    } as RequestInit & { duplex: "half" }
  );

  const headers = new Headers();
  const contentType =
    response.headers.get("Content-Type") ?? "text/event-stream; charset=utf-8";

  headers.set("Content-Type", contentType);
  headers.set(
    "Cache-Control",
    response.headers.get("Cache-Control") ?? "no-cache, no-transform"
  );
  headers.set(
    "Connection",
    response.headers.get("Connection") ?? "keep-alive"
  );
  headers.set(
    "X-Accel-Buffering",
    response.headers.get("X-Accel-Buffering") ?? "no"
  );

  return new Response(response.body, {
    status: response.status,
    headers
  });
}
