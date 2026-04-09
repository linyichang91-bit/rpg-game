import { proxyToEngine } from "@/lib/server-proxy";

export async function POST(request: Request) {
  return proxyToEngine(request, "/api/game/start");
}
