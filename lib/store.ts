"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type {
  AuditPacket,
  ExecutedEvent,
  GameState,
  GameTurnResponse,
  MutationLog,
  StoryLog
} from "@/lib/types";

type SandboxStore = {
  sessionId: string | null;
  gameState: GameState | null;
  storyLogs: StoryLog[];
  auditTrail: AuditPacket[];
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

const initialState = {
  sessionId: null,
  gameState: null,
  storyLogs: [] as StoryLog[],
  auditTrail: [] as AuditPacket[],
  isLoading: false,
  errorMessage: null,
  auditPanelOpen: false,
  worldPrompt: ""
};

export const useSandboxStore = create<SandboxStore>()(
  persist(
    (set) => ({
      ...initialState,
      setWorldPrompt: (prompt) => set({ worldPrompt: prompt }),
      setLoading: (isLoading) => set({ isLoading }),
      setError: (message) => set({ errorMessage: message }),
      startSession: (response, prompt) =>
        set(() => {
          const initialLogs = [
            createStoryLog("system", response.narration, true)
          ];
          const initialAudit = createAuditPacket(
            response.current_state,
            response.executed_events,
            response.mutation_logs
          );

          return {
            sessionId: response.session_id,
            gameState: response.current_state,
            storyLogs: initialLogs,
            auditTrail: initialAudit ? [initialAudit] : [],
            worldPrompt: prompt,
            errorMessage: null
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
      toggleAuditPanel: () =>
        set((state) => ({ auditPanelOpen: !state.auditPanelOpen })),
      reset: () => set({ ...initialState })
    }),
    {
      name: "fanfic-sandbox-session",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        sessionId: state.sessionId,
        gameState: state.gameState,
        storyLogs: state.storyLogs,
        auditTrail: state.auditTrail,
        auditPanelOpen: state.auditPanelOpen,
        worldPrompt: state.worldPrompt
      })
    }
  )
);
