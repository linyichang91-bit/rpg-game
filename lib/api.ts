import type {
  GameActionRequest,
  GameActionStreamRequest,
  GameResetRequest,
  GameResetResponse,
  GameRestoreRequest,
  GameRestoreResponse,
  GameSaveRequest,
  GameSaveResponse,
  GameStartRequest,
  GameTurnResponse,
  GameTurnStreamEvent,
  TurnCompletedEvent,
  TurnErrorEvent,
  WorldGenerateResponse
} from "@/lib/types";

type SubmitActionStreamOptions = {
  onEvent?: (event: GameTurnStreamEvent) => void;
  /** Signal to abort the in-flight request. Useful for component unmount or deduplication. */
  signal?: AbortSignal;
};

type TurnRequestErrorOptions = {
  code?: string;
  retryable?: boolean;
};

export class TurnRequestError extends Error {
  code: string;
  retryable: boolean;

  constructor(message: string, options: TurnRequestErrorOptions = {}) {
    super(message);
    this.name = "TurnRequestError";
    this.code = options.code ?? "unknown_error";
    this.retryable = options.retryable ?? false;
  }
}

function isRetryableHttpStatus(status: number): boolean {
  return (
    status >= 500 ||
    status === 408 ||
    status === 409 ||
    status === 425 ||
    status === 429
  );
}

function toTurnRequestError(error: unknown): TurnRequestError {
  if (error instanceof TurnRequestError) {
    return error;
  }

  if (error instanceof Error) {
    return new TurnRequestError(error.message, {
      code: error.name === "AbortError" ? "request_aborted" : "network_error",
      retryable: true
    });
  }

  return new TurnRequestError("回合请求失败。", {
    code: "unknown_error",
    retryable: true
  });
}

async function parseJsonBody<TResponse>(response: Response): Promise<TResponse> {
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
    const fallbackMessage = `请求失败，状态码 ${response.status}。`;
    const errorMessage =
      typeof data.error === "string"
        ? data.error
        : typeof data.detail === "string"
          ? data.detail
          : fallbackMessage;

    throw new TurnRequestError(errorMessage, {
      code: `http_${response.status}`,
      retryable: isRetryableHttpStatus(response.status)
    });
  }

  return data as TResponse;
}

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

  return parseJsonBody<TResponse>(response);
}

function isStreamResponse(response: Response): boolean {
  const contentType = response.headers.get("Content-Type") ?? "";
  return contentType.includes("text/event-stream");
}

function extractEventBlocks(buffer: string): {
  nextBuffer: string;
  rawEvents: string[];
} {
  const normalizedBuffer = buffer.replace(/\r\n/g, "\n");
  const rawEvents: string[] = [];
  let searchBuffer = normalizedBuffer;
  let boundaryIndex = searchBuffer.indexOf("\n\n");

  while (boundaryIndex >= 0) {
    rawEvents.push(searchBuffer.slice(0, boundaryIndex));
    searchBuffer = searchBuffer.slice(boundaryIndex + 2);
    boundaryIndex = searchBuffer.indexOf("\n\n");
  }

  return {
    nextBuffer: searchBuffer,
    rawEvents
  };
}

function parseEventBlock(rawEvent: string): GameTurnStreamEvent | null {
  const lines = rawEvent
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean);

  if (lines.length === 0) {
    return null;
  }

  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith(":")) {
      continue;
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return JSON.parse(dataLines.join("\n")) as GameTurnStreamEvent;
}

function eventToTurnResponse(event: TurnCompletedEvent): GameTurnResponse {
  return {
    session_id: event.session_id,
    current_state: event.current_state,
    narration: event.narration,
    executed_events: event.executed_events,
    mutation_logs: event.mutation_logs,
    telemetry: event.telemetry ?? null
  };
}

async function consumeTurnStream(
  response: Response,
  options: SubmitActionStreamOptions
): Promise<GameTurnResponse> {
  if (!response.body) {
    throw new TurnRequestError("流式响应当前不可读。", {
      code: "stream_unavailable",
      retryable: true
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completedResponse: GameTurnResponse | null = null;
  let streamError: TurnErrorEvent | null = null;

  try {
    while (true) {
      // Re-check abort before each read.
      if (options.signal?.aborted) {
        reader.cancel();
        throw new TurnRequestError("回合请求已被取消。", {
          code: "request_aborted",
          retryable: true,
        });
      }

      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

      const { nextBuffer, rawEvents } = extractEventBlocks(buffer);
      buffer = nextBuffer;

      for (const rawEvent of rawEvents) {
        const event = parseEventBlock(rawEvent);
        if (!event) {
          continue;
        }

        options.onEvent?.(event);

        if (event.type === "turn.completed") {
          completedResponse = eventToTurnResponse(event);
        }

        if (event.type === "turn.error") {
          streamError = event;
        }
      }

      if (done) {
        break;
      }
    }

    const trailingEvent = parseEventBlock(buffer.trim());
    if (trailingEvent) {
      options.onEvent?.(trailingEvent);

      if (trailingEvent.type === "turn.completed") {
        completedResponse = eventToTurnResponse(trailingEvent);
      }

      if (trailingEvent.type === "turn.error") {
        streamError = trailingEvent;
      }
    }

    if (streamError) {
      throw new TurnRequestError(streamError.message, {
        code: streamError.code,
        retryable: streamError.retryable,
      });
    }

    if (!completedResponse) {
      throw new TurnRequestError("流式连接在回合完成前中断了。", {
        code: "stream_incomplete",
        retryable: true,
      });
    }

    return completedResponse;
  } catch (err) {
    reader.cancel();
    throw err;
  }
}

async function requestStream(
  payload: GameActionStreamRequest,
  signal?: AbortSignal
): Promise<Response> {
  return fetch("/api/game/action/stream", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload),
    cache: "no-store",
    signal
  });
}

export function generateWorld(prompt: string): Promise<WorldGenerateResponse> {
  return postJson<WorldGenerateResponse>("/api/world/generate", { prompt });
}

export function startGame(payload: GameStartRequest): Promise<GameTurnResponse> {
  return postJson<GameTurnResponse>("/api/game/start", payload);
}

export async function submitAction(
  payload: GameActionRequest
): Promise<GameTurnResponse> {
  return postJson<GameTurnResponse>("/api/game/action", payload);
}

export async function submitActionStream(
  payload: GameActionStreamRequest,
  options: SubmitActionStreamOptions = {}
): Promise<GameTurnResponse> {
  try {
    const response = await requestStream(payload, options.signal);

    if ([404, 405, 501].includes(response.status)) {
      return submitAction(payload);
    }

    if (!response.ok) {
      return parseJsonBody<GameTurnResponse>(response);
    }

    if (!isStreamResponse(response)) {
      return parseJsonBody<GameTurnResponse>(response);
    }

    return consumeTurnStream(response, options);
  } catch (error) {
    throw toTurnRequestError(error);
  }
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
