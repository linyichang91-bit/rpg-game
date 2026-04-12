"use client";

import { startTransition, useState } from "react";

import { exportGameSave, resetGameSession, restoreGame } from "@/lib/api";
import { useSandboxStore } from "@/lib/store";
import type { SaveSlot } from "@/lib/types";

type SessionVaultProps = {
  compact?: boolean;
};

function formatSavedAt(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(timestamp);
}

function getLocationLabel(slot: SaveSlot): string {
  const { game_state } = slot.snapshot;
  return (
    game_state.world_config.topology.nodes[game_state.current_location_id]?.title ??
    game_state.current_location_id
  );
}

function getLatestNarration(slot: SaveSlot): string {
  const latestSystemLog = [...slot.snapshot.story_logs]
    .reverse()
    .find((entry) => entry.role === "system");

  return latestSystemLog?.text.trim() ?? "该存档尚未记录旁白。";
}

export function SessionVault({ compact = false }: SessionVaultProps) {
  const sessionId = useSandboxStore((state) => state.sessionId);
  const gameState = useSandboxStore((state) => state.gameState);
  const saveSlots = useSandboxStore((state) => state.saveSlots);
  const isLoading = useSandboxStore((state) => state.isLoading);
  const setLoading = useSandboxStore((state) => state.setLoading);
  const setError = useSandboxStore((state) => state.setError);
  const createSaveSlot = useSandboxStore((state) => state.createSaveSlot);
  const restoreFromSaveSlot = useSandboxStore((state) => state.restoreFromSaveSlot);
  const deleteSaveSlot = useSandboxStore((state) => state.deleteSaveSlot);
  const clearSaveSlots = useSandboxStore((state) => state.clearSaveSlots);
  const reset = useSandboxStore((state) => state.reset);
  const [isOpen, setIsOpen] = useState(false);

  async function handleSave() {
    if (!sessionId || !gameState || isLoading) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await exportGameSave({ session_id: sessionId });
      createSaveSlot(response.runtime_snapshot);
      setIsOpen(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存存档失败。";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoad(slot: SaveSlot) {
    if (isLoading) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await restoreGame({
        world_prompt: slot.snapshot.world_prompt,
        game_state: slot.snapshot.game_state,
        runtime_snapshot: slot.snapshot.runtime_snapshot
      });

      startTransition(() => {
        restoreFromSaveSlot(slot, response);
      });
      setIsOpen(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读档失败。";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  async function handleResetCurrent() {
    if (isLoading) {
      return;
    }

    if (!window.confirm("重置当前冒险会清空未保存进度，确定继续吗？")) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      if (sessionId) {
        await resetGameSession({ session_id: sessionId });
      }
    } catch {
      // Local reset should still proceed even if the server session is already gone.
    } finally {
      startTransition(() => {
        reset();
      });
      setLoading(false);
    }
  }

  function handleDelete(slotId: string) {
    if (!window.confirm("删除这个存档后将无法恢复，确定继续吗？")) {
      return;
    }
    deleteSaveSlot(slotId);
  }

  function handleClearAll() {
    if (!window.confirm("这会清空全部本地存档，确定继续吗？")) {
      return;
    }
    clearSaveSlots();
  }

  return (
    <div className="session-vault-container">
      <section className={`session-toolbar panel${compact ? " compact" : ""}`}>
        {!compact ? (
          <div className="session-toolbar-copy">
            <strong className="session-toolbar-title">
              {gameState ? "当前冒险在线" : "等待载入或开新局"}
            </strong>
            <span className="session-toolbar-meta">
              {`本地存档 ${saveSlots.length} 个`}
            </span>
          </div>
        ) : null}

        <div className="session-toolbar-actions">
          {gameState ? (
            <button
              className="secondary-action"
              disabled={isLoading}
              onClick={handleSave}
              type="button"
            >
              {compact ? "保存" : "保存进度"}
            </button>
          ) : null}
          <button
            className="secondary-action"
            onClick={() => setIsOpen((current) => !current)}
            type="button"
          >
            {isOpen ? "收起存档" : compact ? `存档 ${saveSlots.length}` : "查看存档"}
          </button>
          {gameState ? (
            <button
              className="secondary-action danger-action"
              disabled={isLoading}
              onClick={handleResetCurrent}
              type="button"
            >
              重置
            </button>
          ) : null}
        </div>
      </section>

      {isOpen ? (
        <section className="save-vault panel">
          <div className="save-vault-header">
            <div>
              <span className="panel-kicker">存档列表</span>
              <h2>可恢复进度</h2>
            </div>
            {saveSlots.length > 0 ? (
              <button
                className="secondary-action subtle-action"
                onClick={handleClearAll}
                type="button"
              >
                清空全部
              </button>
            ) : null}
          </div>

          {saveSlots.length === 0 ? (
            <p className="empty-copy">
              还没有手动存档。进入冒险后点“保存进度”，这里就会出现可读档槽位。
            </p>
          ) : (
            <div className="save-slot-list">
              {saveSlots.map((slot) => (
                <article className="save-slot-card" key={slot.id}>
                  <div className="save-slot-top">
                    <div>
                      <h3>{slot.label}</h3>
                      <p>
                        {`${slot.snapshot.game_state.world_config.theme} · ${getLocationLabel(
                          slot
                        )}`}
                      </p>
                    </div>
                    <span className="save-slot-time">
                      {formatSavedAt(slot.saved_at)}
                    </span>
                  </div>

                  <p className="save-slot-preview">{getLatestNarration(slot)}</p>

                  <div className="save-slot-actions">
                    <button
                      className="secondary-action"
                      disabled={isLoading}
                      onClick={() => handleLoad(slot)}
                      type="button"
                    >
                      读取此档
                    </button>
                    <button
                      className="secondary-action subtle-action"
                      onClick={() => handleDelete(slot.id)}
                      type="button"
                    >
                      删除
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
