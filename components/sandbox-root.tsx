"use client";

import { GenesisView } from "@/components/genesis-view";
import { SandboxConsole } from "@/components/sandbox-console";
import { SessionVault } from "@/components/session-vault";
import { useSandboxStore } from "@/lib/store";

export function SandboxRoot() {
  const gameState = useSandboxStore((state) => state.gameState);
  const errorMessage = useSandboxStore((state) => state.errorMessage);
  const isLoading = useSandboxStore((state) => state.isLoading);
  const activeTurnStream = useSandboxStore((state) => state.activeTurnStream);
  const reset = useSandboxStore((state) => state.reset);

  return (
    <main className={`app-shell${gameState ? "" : " is-genesis"}`}>
      <div className="background-layers" aria-hidden="true">
        <span className="background-orb orb-left" />
        <span className="background-orb orb-right" />
        <span className="background-grid" />
      </div>

      {errorMessage ? (
        <div className="error-banner">
          <span>{errorMessage}</span>
          <button onClick={reset} type="button">
            重置会话
          </button>
        </div>
      ) : null}

      {gameState ? (
        <SandboxConsole />
      ) : (
        <>
          <SessionVault />
          <GenesisView />
        </>
      )}

      {isLoading && !activeTurnStream ? (
        <div className="loading-scrim" aria-hidden="true" />
      ) : null}
    </main>
  );
}
