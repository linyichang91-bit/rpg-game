import type {
  GameActionRequest,
  GameResetRequest,
  GameResetResponse,
  GameRestoreRequest,
  GameRestoreResponse,
  GameSaveRequest,
  GameSaveResponse,
  GameStartRequest,
  GameTurnResponse,
  WorldGenerateResponse
} from "@/lib/types";

async function postJson<TResponse, TPayload extends object = object>(
  url: string,
  payload: TPayload
): Promise<TResponse> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  const text = await response.text();
  let data: Record<string, unknown> = {};

  if (text) {
    try {
      data = JSON.parse(text) as Record<string, unknown>;
    } catch {
      data = { error: text.trim() };
    }
  }

  if (!response.ok) {
    const errorMessage =
      typeof data.error === "string"
        ? data.error
        : typeof data.detail === "string"
          ? data.detail
        : `请求失败，状态码 ${response.status}。`;
    throw new Error(errorMessage);
  }

  return data as TResponse;
}

export function generateWorld(prompt: string): Promise<WorldGenerateResponse> {
  return postJson<WorldGenerateResponse>("/api/world/generate", { prompt });
}

export function startGame(
  payload: GameStartRequest
): Promise<GameTurnResponse> {
  return postJson<GameTurnResponse>("/api/game/start", payload);
}

export function submitAction(
  payload: GameActionRequest
): Promise<GameTurnResponse> {
  return postJson<GameTurnResponse>("/api/game/action", payload);
}

export function exportGameSave(
  payload: GameSaveRequest
): Promise<GameSaveResponse> {
  return postJson<GameSaveResponse>("/api/game/save", payload);
}

export function restoreGame(
  payload: GameRestoreRequest
): Promise<GameRestoreResponse> {
  return postJson<GameRestoreResponse>("/api/game/restore", payload);
}

export function resetGameSession(
  payload: GameResetRequest
): Promise<GameResetResponse> {
  return postJson<GameResetResponse>("/api/game/reset", payload);
}
