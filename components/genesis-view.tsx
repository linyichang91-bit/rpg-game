"use client";

import { FormEvent, startTransition, useDeferredValue, useEffect, useState } from "react";

import { generateWorld, startGame } from "@/lib/api";
import { useSandboxStore } from "@/lib/store";

type GenesisStageStatus = "pending" | "active" | "done" | "failed";

type GenesisStage = {
  id: "world_generate" | "game_start";
  label: string;
  detail: string;
  status: GenesisStageStatus;
  durationMs: number | null;
};

function createInitialStages(): GenesisStage[] {
  return [
    {
      id: "world_generate",
      label: "世界织布机",
      detail: "等待你提交设定后开始编译时间线、宏观局势和开局锚点。",
      status: "pending",
      durationMs: null
    },
    {
      id: "game_start",
      label: "第一幕叙事",
      detail: "等待世界设定完成后，再生成序章和第一段叙事。",
      status: "pending",
      durationMs: null
    }
  ];
}

function formatDuration(durationMs: number): string {
  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }

  if (durationMs < 10_000) {
    return `${(durationMs / 1000).toFixed(2)}s`;
  }

  return `${(durationMs / 1000).toFixed(1)}s`;
}

function getStageStatusLabel(status: GenesisStageStatus): string {
  if (status === "done") {
    return "已完成";
  }

  if (status === "active") {
    return "进行中";
  }

  if (status === "failed") {
    return "失败";
  }

  return "待开始";
}

export function GenesisView() {
  const [prompt, setPrompt] = useState("");
  const [stages, setStages] = useState<GenesisStage[]>(createInitialStages);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [lastTotalMs, setLastTotalMs] = useState<number | null>(null);
  const deferredPrompt = useDeferredValue(prompt);
  const setLoading = useSandboxStore((state) => state.setLoading);
  const setError = useSandboxStore((state) => state.setError);
  const setWorldPrompt = useSandboxStore((state) => state.setWorldPrompt);
  const startSession = useSandboxStore((state) => state.startSession);
  const isLoading = useSandboxStore((state) => state.isLoading);

  useEffect(() => {
    if (!isLoading || runStartedAt === null) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setElapsedMs(Date.now() - runStartedAt);
    }, 120);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [isLoading, runStartedAt]);

  const displayTotalMs =
    isLoading && runStartedAt !== null ? elapsedMs : lastTotalMs ?? 0;
  const completedDurationMs = stages.reduce((total, stage) => {
    if (stage.status !== "done" || stage.durationMs === null) {
      return total;
    }

    return total + stage.durationMs;
  }, 0);
  const activeStage = stages.find((stage) => stage.status === "active") ?? null;
  const activeStageElapsedMs =
    activeStage === null ? 0 : Math.max(0, displayTotalMs - completedDurationMs);
  const showProgress =
    isLoading ||
    lastTotalMs !== null ||
    stages.some((stage) => stage.status !== "pending");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isLoading) {
      return;
    }

    const flowStartedAt = performance.now();

    setLoading(true);
    setError(null);
    setWorldPrompt(trimmedPrompt);
    setElapsedMs(0);
    setLastTotalMs(null);
    setRunStartedAt(Date.now());
    setStages([
      {
        id: "world_generate",
        label: "世界织布机",
        detail: "正在锚定时间线、编织宏观局势和 opening scene。",
        status: "active",
        durationMs: null
      },
      {
        id: "game_start",
        label: "第一幕叙事",
        detail: "等待世界设定完成后启动。",
        status: "pending",
        durationMs: null
      }
    ]);

    try {
      const worldStartedAt = performance.now();
      const worldResponse = await generateWorld(trimmedPrompt);
      const worldDurationMs =
        worldResponse.telemetry?.total_ms ??
        Math.round(performance.now() - worldStartedAt);

      setStages([
        {
          id: "world_generate",
          label: "世界织布机",
          detail: "世界设定已经编译完成，时间线锚点与战役上下文已就绪。",
          status: "done",
          durationMs: worldDurationMs
        },
        {
          id: "game_start",
          label: "第一幕叙事",
          detail: "正在把玩家投入开局场景，生成第一段叙事。",
          status: "active",
          durationMs: null
        }
      ]);

      const startStartedAt = performance.now();
      const startResponse = await startGame({
        world_config: worldResponse.world_config,
        world_prompt: trimmedPrompt
      });
      const openingDurationMs =
        startResponse.telemetry?.total_ms ??
        Math.round(performance.now() - startStartedAt);
      const totalMs = Math.round(performance.now() - flowStartedAt);

      setStages([
        {
          id: "world_generate",
          label: "世界织布机",
          detail: "世界设定已经编译完成，时间线锚点与战役上下文已就绪。",
          status: "done",
          durationMs: worldDurationMs
        },
        {
          id: "game_start",
          label: "第一幕叙事",
          detail: "序章已落地，第一幕场景已经加载完成。",
          status: "done",
          durationMs: openingDurationMs
        }
      ]);
      setLastTotalMs(totalMs);

      startTransition(() => {
        startSession(startResponse, trimmedPrompt);
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "世界启动失败。";

      setLastTotalMs(Math.round(performance.now() - flowStartedAt));
      setStages((currentStages) =>
        currentStages.map((stage) =>
          stage.status === "active"
            ? {
                ...stage,
                status: "failed",
                detail: message
              }
            : stage
        )
      );
      setError(message);
    } finally {
      setLoading(false);
      setRunStartedAt(null);
    }
  }

  return (
    <section className="genesis-shell">
      <div className="genesis-card">
        <div className="panel-kicker">世界织布机</div>
        <h1 className="genesis-title">描述你想投身其中的同人世界。</h1>
        <p className="genesis-copy">
          写下一个 AU、跨界脑洞，或原创设定。引擎会先编织世界规则，再把你直接送进第一幕。
        </p>

        <form className="genesis-form" onSubmit={handleSubmit}>
          <textarea
            className="genesis-textarea"
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="描述你想创造的同人世界、主角身份、关键转折与整体氛围……"
            rows={8}
            value={prompt}
          />

          <div className="genesis-footer">
            <span className="genesis-hint">
              {deferredPrompt.trim().length > 0
                ? `已准备 ${deferredPrompt.trim().length} 个字的世界设定。`
                : "一个好提示通常会写清原著 IP、魔改点，以及主角切入点。"}
            </span>
            <button
              className="genesis-submit"
              disabled={!deferredPrompt.trim() || isLoading}
              type="submit"
            >
              {isLoading ? "创世引擎正在编织中…" : "生成并进入世界"}
            </button>
          </div>
        </form>

        {showProgress ? (
          <section className="genesis-progress panel" aria-live="polite">
            <div className="genesis-progress-header">
              <div>
                <div className="panel-kicker">生成示波器</div>
                <h2 className="genesis-progress-title">世界生成进度</h2>
              </div>
              <div className="genesis-progress-total">
                <span>总耗时</span>
                <strong>{formatDuration(displayTotalMs)}</strong>
              </div>
            </div>

            <p className="genesis-progress-copy">
              世界生成通常会经历两段明显耗时：先调用世界织布机编译设定，再生成第一幕叙事。现在至少能看清它卡在哪一步。
            </p>

            <div className="genesis-progress-pulse">
              <span>{`当前阶段：${activeStage?.label ?? "已完成"}`}</span>
              <span>{`已完成 ${stages.filter((stage) => stage.status === "done").length}/${stages.length}`}</span>
            </div>

            <div className="genesis-stage-list">
              {stages.map((stage, index) => {
                const stageDuration =
                  stage.status === "active"
                    ? activeStageElapsedMs
                    : stage.durationMs;

                return (
                  <article
                    className={`genesis-stage is-${stage.status}`}
                    key={stage.id}
                  >
                    <div className="genesis-stage-top">
                      <div className="genesis-stage-index">{index + 1}</div>
                      <div className="genesis-stage-body">
                        <div className="genesis-stage-heading">
                          <h3>{stage.label}</h3>
                          <span>{getStageStatusLabel(stage.status)}</span>
                        </div>
                        <p>{stage.detail}</p>
                      </div>
                      <div className="genesis-stage-time">
                        {stageDuration !== null ? formatDuration(stageDuration) : "--"}
                      </div>
                    </div>
                    <div className="genesis-stage-track" aria-hidden="true">
                      <span />
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
