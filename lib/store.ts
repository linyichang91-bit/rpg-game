"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type {
  AuditPacket,
  ExecutedEvent,
  GameRestoreResponse,
  GameState,
  GameTurnResponse,
  MutationLog,
  RuntimeSessionSnapshot,
  SaveSlot,
  StoryLog
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
  setWorldPrompt: (prompt: string) => void;
  setLoading: (isLoading: boolean) => void;
  setError: (message: string | null) => void;
  startSession: (response: GameTurnResponse, prompt: string) => void;
  appendUserLog: (text: string) => void;
  resolveTurn: (response: GameTurnResponse) => void;
  pushSystemNotice: (text: string) => void;
  createSaveSlot: (runtimeSnapshot: RuntimeSessionSnapshot, label?: string) => SaveSlot | null;
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
  sessionId: null,
  gameState: null,
  storyLogs: [] as StoryLog[],
  auditTrail: [] as AuditPacket[],
  isLoading: false,
  errorMessage: null,
  auditPanelOpen: false,
  worldPrompt: ""
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

export const useSandboxStore = create<SandboxStore>()(
  persist(
    (set) => ({
      ...initialState,
      setWorldPrompt: (prompt) => set({ worldPrompt: prompt }),
      setLoading: (isLoading) => set({ isLoading }),
      setError: (message) => set({ errorMessage: message }),
      startSession: (response, prompt) =>
        set(() => {
          const initialLogs = [createStoryLog("system", response.narration, true)];
          const initialAudit = createAuditPacket(
            response.current_state,
            response.executed_events,
            response.mutation_logs
          );

          return {
            ...runtimeInitialState,
            sessionId: response.session_id,
            gameState: response.current_state,
            storyLogs: initialLogs,
            auditTrail: initialAudit ? [initialAudit] : [],
            worldPrompt: prompt
          };
        }),
      appendUserLog: (text) =>
        set((state) => ({
          storyLogs: [...state.storyLogs, createStoryLog("user", text, false)],
          errorMessage: null
        })),
      resolveTurn: (response) =>
        set((state) => {
          const auditPacket = createAuditPacket(
            response.current_state,
            response.executed_events,
            response.mutation_logs
          );

          return {
            sessionId: response.session_id,
            gameState: response.current_state,
            storyLogs: [
              ...state.storyLogs,
              createStoryLog("system", response.narration, true)
            ],
            auditTrail: auditPacket
              ? [auditPacket, ...state.auditTrail].slice(0, 12)
              : state.auditTrail,
            errorMessage: null
          };
        }),
      pushSystemNotice: (text) =>
        set((state) => ({
          storyLogs: [...state.storyLogs, createStoryLog("system", text, true)]
        })),
      createSaveSlot: (runtimeSnapshot, label) => {
        let createdSlot: SaveSlot | null = null;

        set((state) => {
          if (!state.gameState) {
            return state;
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

          return {
            saveSlots: [createdSlot, ...state.saveSlots].slice(0, 12),
            errorMessage: null
          };
        });

        return createdSlot;
      },
      restoreFromSaveSlot: (slot, response) =>
        set((state) => ({
          ...runtimeInitialState,
          saveSlots: state.saveSlots,
          sessionId: response.session_id,
          gameState: response.current_state,
          storyLogs: normalizeStoryLogs(cloneSerializable(slot.snapshot.story_logs)),
          auditTrail: cloneSerializable(slot.snapshot.audit_trail),
          auditPanelOpen: slot.snapshot.audit_panel_open,
          worldPrompt: slot.snapshot.world_prompt
        })),
      deleteSaveSlot: (slotId) =>
        set((state) => ({
          saveSlots: state.saveSlots.filter((slot) => slot.id !== slotId)
        })),
      clearSaveSlots: () => set({ saveSlots: [] }),
      toggleAuditPanel: () =>
        set((state) => ({ auditPanelOpen: !state.auditPanelOpen })),
      reset: () =>
        set((state) => ({
          ...runtimeInitialState,
          saveSlots: state.saveSlots
        }))
    }),
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
