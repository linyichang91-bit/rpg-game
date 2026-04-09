"use client";

import { startTransition } from "react";

import { AuditPanel } from "@/components/audit-panel";
import { CommandTerminal } from "@/components/command-terminal";
import { NarrativePanel } from "@/components/narrative-panel";
import { StatusPanel } from "@/components/status-panel";
import { submitAction } from "@/lib/api";
import { getVitalsSummary } from "@/lib/format";
import { useSandboxStore } from "@/lib/store";

export function SandboxConsole() {
  const sessionId = useSandboxStore((state) => state.sessionId);
  const gameState = useSandboxStore((state) => state.gameState);
  const storyLogs = useSandboxStore((state) => state.storyLogs);
  const auditTrail = useSandboxStore((state) => state.auditTrail);
  const isLoading = useSandboxStore((state) => state.isLoading);
  const auditPanelOpen = useSandboxStore((state) => state.auditPanelOpen);
  const setLoading = useSandboxStore((state) => state.setLoading);
  const setError = useSandboxStore((state) => state.setError);
  const appendUserLog = useSandboxStore((state) => state.appendUserLog);
  const resolveTurn = useSandboxStore((state) => state.resolveTurn);
  const pushSystemNotice = useSandboxStore((state) => state.pushSystemNotice);
  const toggleAuditPanel = useSandboxStore((state) => state.toggleAuditPanel);

  if (!sessionId || !gameState) {
    return null;
  }

  const activeSessionId = sessionId;

  async function handleCommand(command: string) {
    appendUserLog(command);
    setLoading(true);
    setError(null);

    try {
      const response = await submitAction({
        session_id: activeSessionId,
        user_input: command
      });

      startTransition(() => {
        resolveTurn(response);
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "指令结算失败。";
      setError(message);
      pushSystemNotice(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="console-shell">
      <header className="console-ribbon">
        <div>
          <span className="panel-kicker">
            {gameState.world_config.fanfic_meta.base_ip}
          </span>
          <h1>{gameState.world_config.theme}</h1>
        </div>
        <div className="ribbon-meta">
          <span>{`会话 ${gameState.session_id}`}</span>
          <span>{`状态 ${getVitalsSummary(gameState)}`}</span>
        </div>
      </header>

      <div className="console-grid">
        <StatusPanel gameState={gameState} />
        <NarrativePanel isLoading={isLoading} storyLogs={storyLogs}>
          <CommandTerminal isLoading={isLoading} onSubmit={handleCommand} />
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
