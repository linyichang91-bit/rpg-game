"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { immer } from "zustand/middleware/immer";

import type {
  ActiveTurnStream,
  AuditPacket,
  ExecutedEvent,
  GameRestoreResponse,
  GameState,
  GameTurnResponse,
  GameTurnStreamEvent,
  MutationLog,
  PendingStoryLog,
  RuntimeSessionSnapshot,
  SaveSlot,
  StoryLog,
  TurnFailureState
} from "@/lib/types";

type SandboxStore = {
  sessionId: string | null;
  gameState: GameState | null;
  storyLogs: StoryLog[];
  auditTrail: AuditPacket[];
  saveSlots: SaveSlot[];
  isLoading: boolean;
  errorMessage: string | null;
  auditPanelOpen: boolean;
  worldPrompt: string;
  activeTurnStream: ActiveTurnStream | null;
  lastSubmittedCommand: string | null;
  turnFailure: TurnFailureState | null;
  setWorldPrompt: (prompt: string) => void;
  setLoading: (isLoading: boolean) => void;
  setError: (message: string | null) => void;
  startSession: (response: GameTurnResponse, prompt: string) => void;
  appendUserLog: (text: string) => void;
  rememberSubmittedCommand: (command: string) => void;
  beginTurnStream: (clientTurnId: string) => void;
  applyTurnStreamEvent: (event: GameTurnStreamEvent) => void;
  clearTurnStream: () => void;
  recordTurnFailure: (
    command: string,
    message: string,
    retryable: boolean
  ) => void;
  clearTurnFailure: () => void;
  resolveTurn: (response: GameTurnResponse) => void;
  pushSystemNotice: (text: string) => void;
  createSaveSlot: (
    runtimeSnapshot: RuntimeSessionSnapshot,
    label?: string
  ) => SaveSlot | null;
  restoreFromSaveSlot: (slot: SaveSlot, response: GameRestoreResponse) => void;
  deleteSaveSlot: (slotId: string) => void;
  clearSaveSlots: () => void;
  toggleAuditPanel: () => void;
  reset: () => void;
};

const createStoryLog = (
  role: StoryLog["role"],
  text: string,
  animate = false
): StoryLog => ({
  id: globalThis.crypto?.randomUUID?.() ?? `${role}-${Date.now()}`,
  role,
  text,
  animate,
  timestamp: Date.now()
});

const createAuditPacket = (
  currentState: GameState | undefined,
  executedEvents: ExecutedEvent[] | undefined,
  mutationLogs: MutationLog[] | undefined
): AuditPacket | null => {
  if (!executedEvents?.length && !mutationLogs?.length) {
    return null;
  }

  return {
    id: globalThis.crypto?.randomUUID?.() ?? `audit-${Date.now()}`,
    created_at: Date.now(),
    executed_events: executedEvents ?? [],
    mutation_logs: mutationLogs ?? [],
    topology_snapshot: currentState?.world_config.topology ?? null
  };
};

const runtimeInitialState = {
  sessionId: null as string | null,
  gameState: null as GameState | null,
  storyLogs: [] as StoryLog[],
  auditTrail: [] as AuditPacket[],
  isLoading: false,
  errorMessage: null as string | null,
  auditPanelOpen: false,
  worldPrompt: "",
  activeTurnStream: null as ActiveTurnStream | null,
  lastSubmittedCommand: null as string | null,
  turnFailure: null as TurnFailureState | null
};

const initialState = {
  ...runtimeInitialState,
  saveSlots: [] as SaveSlot[]
};

function cloneSerializable<T>(value: T): T {
  if (typeof globalThis.structuredClone === "function") {
    return globalThis.structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function normalizeStoryLogs(logs: StoryLog[]): StoryLog[] {
  return logs.map((entry) => ({
    ...entry,
    animate: false
  }));
}

function normalizeSaveSlots(saveSlots: SaveSlot[]): SaveSlot[] {
  return saveSlots.map((slot) => ({
    ...slot,
    snapshot: {
      ...slot.snapshot,
      story_logs: normalizeStoryLogs(slot.snapshot.story_logs)
    }
  }));
}

function getCurrentLocationTitle(gameState: GameState): string {
  return (
    gameState.world_config.topology.nodes[gameState.current_location_id]?.title ??
    gameState.current_location_id
  );
}

function buildSaveLabel(gameState: GameState, label?: string): string {
  const trimmedLabel = label?.trim();
  if (trimmedLabel) {
    return trimmedLabel;
  }

  return [
    gameState.world_config.fanfic_meta.base_ip,
    getCurrentLocationTitle(gameState)
  ].join(" · ");
}

function createPendingStoryLog(messageId: string): PendingStoryLog {
  return {
    id: messageId,
    role: "system",
    text: "",
    timestamp: Date.now(),
    isStreaming: true
  };
}

function createTurnFailureState(
  command: string,
  message: string,
  retryable: boolean
): TurnFailureState {
  return {
    id: globalThis.crypto?.randomUUID?.() ?? `turn-failure-${Date.now()}`,
    command,
    message,
    retryable,
    createdAt: Date.now()
  };
}

export const useSandboxStore = create<SandboxStore>()(
  persist(
    immer((set) => ({
      ...initialState,
      setWorldPrompt: (prompt) =>
        set((state) => {
          state.worldPrompt = prompt;
        }),
      setLoading: (isLoading) =>
        set((state) => {
          state.isLoading = isLoading;
        }),
      setError: (message) =>
        set((state) => {
          state.errorMessage = message;
        }),
      startSession: (response, prompt) =>
        set((state) => {
          const initialLogs = [createStoryLog("system", response.narration, true)];
          const initialAudit = createAuditPacket(
            response.current_state,
            response.executed_events,
            response.mutation_logs
          );

          // Reset runtime state immutably via immer
          Object.assign(state, {
            ...runtimeInitialState,
            sessionId: response.session_id,
            gameState: response.current_state,
            storyLogs: initialLogs,
            auditTrail: initialAudit ? [initialAudit] : [],
            worldPrompt: prompt
          });
        }),
      appendUserLog: (text) =>
        set((state) => {
          state.storyLogs.push(createStoryLog("user", text, false));
          state.errorMessage = null;
          state.turnFailure = null;
        }),
      rememberSubmittedCommand: (command) =>
        set((state) => {
          state.lastSubmittedCommand = command;
          state.turnFailure = null;
        }),
      beginTurnStream: (clientTurnId) =>
        set((state) => {
          state.activeTurnStream = {
            clientTurnId,
            serverTurnId: null,
            phase: "connecting",
            statusMessage: "正在连接叙事引擎...",
            narration: null
          };
          state.errorMessage = null;
          state.turnFailure = null;
        }),
      applyTurnStreamEvent: (event) =>
        set((state) => {
          const activeTurnStream = state.activeTurnStream;
          if (!activeTurnStream) {
            return;
          }

          if (
            "client_turn_id" in event &&
            event.client_turn_id !== activeTurnStream.clientTurnId
          ) {
            return;
          }

          switch (event.type) {
            case "turn.accepted":
              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = "loading_session";
              activeTurnStream.statusMessage = "回合已受理，正在编排叙事...";
              break;
            case "turn.status":
              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = event.phase;
              activeTurnStream.statusMessage = event.message;
              break;
            case "narration.start":
              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = "writing_narration";
              activeTurnStream.statusMessage = "旁白生成中...";
              activeTurnStream.narration = createPendingStoryLog(event.message_id);
              break;
            case "narration.delta": {
              const existingNarration =
                activeTurnStream.narration ??
                createPendingStoryLog(event.message_id);

              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = "writing_narration";
              activeTurnStream.statusMessage = "旁白生成中...";
              activeTurnStream.narration = {
                ...existingNarration,
                id: event.message_id,
                text: `${existingNarration.text}${event.delta}`
              };
              break;
            }
            case "narration.end": {
              const existingNarration =
                activeTurnStream.narration ??
                createPendingStoryLog(event.message_id);

              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = "finalizing_state";
              activeTurnStream.statusMessage = "正在落定本回合结果...";
              activeTurnStream.narration = {
                ...existingNarration,
                id: event.message_id,
                text: event.full_text
              };
              break;
            }
            case "turn.completed":
              activeTurnStream.serverTurnId = event.server_turn_id;
              activeTurnStream.phase = "finalizing_state";
              activeTurnStream.statusMessage = "本回合已完成。";
              break;
            case "turn.error":
              activeTurnStream.serverTurnId = event.server_turn_id ?? activeTurnStream.serverTurnId;
              activeTurnStream.statusMessage = event.message;
              break;
            case "heartbeat":
              break;
            default:
              break;
          }
        }),
      clearTurnStream: () =>
        set((state) => {
          state.activeTurnStream = null;
        }),
      recordTurnFailure: (command, message, retryable) =>
        set((state) => {
          state.turnFailure = createTurnFailureState(command, message, retryable);
          state.errorMessage = null;
        }),
      clearTurnFailure: () =>
        set((state) => {
          state.turnFailure = null;
        }),
      resolveTurn: (response) =>
        set((state) => {
          const auditPacket = createAuditPacket(
            response.current_state,
            response.executed_events,
            response.mutation_logs
          );
          const shouldAnimateNarration =
            !state.activeTurnStream?.narration?.text.trim().length;

          state.sessionId = response.session_id;
          state.gameState = response.current_state;
          state.storyLogs.push(
            createStoryLog("system", response.narration, shouldAnimateNarration)
          );
          if (auditPacket) {
            state.auditTrail.unshift(auditPacket);
            // Keep only the 12 most recent audit packets
            if (state.auditTrail.length > 12) {
              state.auditTrail.length = 12;
            }
          }
          state.errorMessage = null;
          state.activeTurnStream = null;
          state.turnFailure = null;
        }),
      pushSystemNotice: (text) =>
        set((state) => {
          state.storyLogs.push(createStoryLog("system", text, true));
        }),
      createSaveSlot: (runtimeSnapshot, label) => {
        let createdSlot: SaveSlot | null = null;

        set((state) => {
          if (!state.gameState) {
            return;
          }

          createdSlot = {
            id: globalThis.crypto?.randomUUID?.() ?? `save-${Date.now()}`,
            label: buildSaveLabel(state.gameState, label),
            saved_at: Date.now(),
            snapshot: {
              version: 1,
              world_prompt: state.worldPrompt,
              game_state: cloneSerializable(state.gameState),
              story_logs: normalizeStoryLogs(cloneSerializable(state.storyLogs)),
              audit_trail: cloneSerializable(state.auditTrail),
              audit_panel_open: state.auditPanelOpen,
              runtime_snapshot: cloneSerializable(runtimeSnapshot)
            }
          };

          state.saveSlots.unshift(createdSlot);
          // Keep only the 12 most recent save slots
          if (state.saveSlots.length > 12) {
            state.saveSlots.length = 12;
          }
          state.errorMessage = null;
        });

        return createdSlot;
      },
      restoreFromSaveSlot: (slot, response) =>
        set((state) => {
          // Reset runtime state, preserving saveSlots
          Object.assign(state, {
            ...runtimeInitialState,
            saveSlots: state.saveSlots,
            sessionId: response.session_id,
            gameState: response.current_state,
            storyLogs: normalizeStoryLogs(cloneSerializable(slot.snapshot.story_logs)),
            auditTrail: cloneSerializable(slot.snapshot.audit_trail),
            auditPanelOpen: slot.snapshot.audit_panel_open,
            worldPrompt: slot.snapshot.world_prompt
          });
        }),
      deleteSaveSlot: (slotId) =>
        set((state) => {
          const index = state.saveSlots.findIndex((slot) => slot.id === slotId);
          if (index !== -1) {
            state.saveSlots.splice(index, 1);
          }
        }),
      clearSaveSlots: () =>
        set((state) => {
          state.saveSlots.length = 0;
        }),
      toggleAuditPanel: () =>
        set((state) => {
          state.auditPanelOpen = !state.auditPanelOpen;
        }),
      reset: () =>
        set((state) => {
          const preservedSaveSlots = state.saveSlots;
          Object.assign(state, {
            ...runtimeInitialState,
            saveSlots: preservedSaveSlots
          });
        })
    })),
    {
      name: "fanfic-sandbox-session",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        sessionId: state.sessionId,
        gameState: state.gameState,
        storyLogs: state.storyLogs,
        auditTrail: state.auditTrail,
        saveSlots: state.saveSlots,
        auditPanelOpen: state.auditPanelOpen,
        worldPrompt: state.worldPrompt
      }),
      merge: (persistedState, currentState) => {
        const typedState = persistedState as Partial<SandboxStore> | undefined;
        if (!typedState) {
          return currentState;
        }

        return {
          ...currentState,
          ...typedState,
          storyLogs: typedState.storyLogs
            ? normalizeStoryLogs(typedState.storyLogs)
            : currentState.storyLogs,
          saveSlots: typedState.saveSlots
            ? normalizeSaveSlots(typedState.saveSlots)
            : currentState.saveSlots
        };
      }
    }
  )
);
