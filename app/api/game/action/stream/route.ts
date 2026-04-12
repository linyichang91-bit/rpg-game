import { proxyToEngineStream } from "@/lib/server-proxy";

export async function POST(request: Request) {
  return proxyToEngineStream(request, "/api/game/action/stream");
}
