"use client";

import { startTransition, useEffect, useRef } from "react";

import { AuditPanel } from "@/components/audit-panel";
import { CommandTerminal } from "@/components/command-terminal";
import { NarrativePanel } from "@/components/narrative-panel";
import { SessionVault } from "@/components/session-vault";
import { StatusPanel } from "@/components/status-panel";
import { submitActionStream, TurnRequestError } from "@/lib/api";
import { getVitalsSummary } from "@/lib/format";
import { useSandboxStore } from "@/lib/store";
import type { GameTurnStreamEvent } from "@/lib/types";

type RunCommandOptions = {
  reuseExistingLog?: boolean;
};

export function SandboxConsole() {
  const inFlightTurnRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Cancel any in-flight request when the console unmounts (e.g. user exits game).
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);
  const sessionId = useSandboxStore((state) => state.sessionId);
  const gameState = useSandboxStore((state) => state.gameState);
  const storyLogs = useSandboxStore((state) => state.storyLogs);
  const auditTrail = useSandboxStore((state) => state.auditTrail);
  const isLoading = useSandboxStore((state) => state.isLoading);
  const auditPanelOpen = useSandboxStore((state) => state.auditPanelOpen);
  const activeTurnStream = useSandboxStore((state) => state.activeTurnStream);
  const lastSubmittedCommand = useSandboxStore(
    (state) => state.lastSubmittedCommand
  );
  const turnFailure = useSandboxStore((state) => state.turnFailure);
  const setLoading = useSandboxStore((state) => state.setLoading);
  const setError = useSandboxStore((state) => state.setError);
  const appendUserLog = useSandboxStore((state) => state.appendUserLog);
  const rememberSubmittedCommand = useSandboxStore(
    (state) => state.rememberSubmittedCommand
  );
  const beginTurnStream = useSandboxStore((state) => state.beginTurnStream);
  const applyTurnStreamEvent = useSandboxStore(
    (state) => state.applyTurnStreamEvent
  );
  const clearTurnStream = useSandboxStore((state) => state.clearTurnStream);
  const recordTurnFailure = useSandboxStore((state) => state.recordTurnFailure);
  const clearTurnFailure = useSandboxStore((state) => state.clearTurnFailure);
  const resolveTurn = useSandboxStore((state) => state.resolveTurn);
  const toggleAuditPanel = useSandboxStore((state) => state.toggleAuditPanel);

  if (!sessionId || !gameState) {
    return null;
  }

  const activeSessionId = sessionId;

  function handleStreamEvent(event: GameTurnStreamEvent) {
    startTransition(() => {
      applyTurnStreamEvent(event);
    });
  }

  async function runCommand(command: string, options: RunCommandOptions = {}) {
    const runtimeState = useSandboxStore.getState();
    if (
      inFlightTurnRef.current ||
      runtimeState.isLoading ||
      runtimeState.activeTurnStream
    ) {
      return;
    }
    inFlightTurnRef.current = true;

    // Cancel any in-flight SSE stream before starting a new one.
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const clientTurnId =
      globalThis.crypto?.randomUUID?.() ?? `turn-${Date.now()}`;

    if (!options.reuseExistingLog) {
      appendUserLog(command);
    }

    rememberSubmittedCommand(command);
    clearTurnFailure();
    beginTurnStream(clientTurnId);
    setLoading(true);
    setError(null);

    try {
      const response = await submitActionStream(
        {
          session_id: activeSessionId,
          user_input: command,
          client_turn_id: clientTurnId
        },
        {
          onEvent: handleStreamEvent,
          signal: controller.signal
        }
      );

      startTransition(() => {
        resolveTurn(response);
      });
    } catch (error) {
      const turnError =
        error instanceof TurnRequestError
          ? error
          : new TurnRequestError(
              error instanceof Error ? error.message : "当前回合未能完成。",
              { code: "unknown_error", retryable: true }
            );

      clearTurnStream();
      setError(null);
      recordTurnFailure(command, turnError.message, turnError.retryable);
    } finally {
      inFlightTurnRef.current = false;
      abortControllerRef.current = null;
      setLoading(false);
    }
  }

  async function handleCommand(command: string) {
    await runCommand(command);
  }

  async function handleRetryTurn() {
    if (!lastSubmittedCommand) {
      return;
    }

    await runCommand(lastSubmittedCommand, {
      reuseExistingLog: true
    });
  }

  return (
    <section className="console-shell">
      <header className="console-ribbon">
        <div className="console-ribbon-main">
          <div className="console-ribbon-copy">
            <span className="panel-kicker">
              {gameState.world_config.fanfic_meta.base_ip}
            </span>
            <h1>{gameState.world_config.theme}</h1>
            <div className="ribbon-meta">
              <span>{`会话 ${gameState.session_id}`}</span>
              <span>{`状态 ${getVitalsSummary(gameState)}`}</span>
            </div>
          </div>
          <SessionVault compact />
        </div>
      </header>

      <div className="console-grid">
        <StatusPanel gameState={gameState} />
        <NarrativePanel
          isLoading={isLoading}
          isStreaming={Boolean(activeTurnStream)}
          onRetryTurn={handleRetryTurn}
          pendingStoryLog={activeTurnStream?.narration ?? null}
          retryDisabled={
            !turnFailure?.retryable ||
            !lastSubmittedCommand ||
            isLoading ||
            Boolean(activeTurnStream)
          }
          statusMessage={activeTurnStream?.statusMessage ?? null}
          storyLogs={storyLogs}
          turnFailure={turnFailure}
        >
          <CommandTerminal
            isLoading={isLoading}
            isStreaming={Boolean(activeTurnStream)}
            onSubmit={handleCommand}
          />
        </NarrativePanel>
        <AuditPanel
          auditTrail={auditTrail}
          isOpen={auditPanelOpen}
          onToggle={toggleAuditPanel}
        />
      </div>
    </section>
  );
}
