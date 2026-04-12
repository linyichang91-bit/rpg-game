"use client";

import {
  FormEvent,
  startTransition,
  useDeferredValue,
  useEffect,
  useState
} from "react";

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

type GenesisFormState = {
  worldAndTimeline: string;
  playerGoal: string;
  characterCard: string;
};

const INITIAL_FORM_STATE: GenesisFormState = {
  worldAndTimeline: "",
  playerGoal: "",
  characterCard: ""
};

function createInitialStages(): GenesisStage[] {
  return [
    {
      id: "world_generate",
      label: "世界织布机",
      detail: "等待你提交三段创世信息后，开始编译世界、主线任务和角色属性。",
      status: "pending",
      durationMs: null
    },
    {
      id: "game_start",
      label: "第一幕叙事",
      detail: "等待创世完成后，再生成序章和第一段可游玩的开场旁白。",
      status: "pending",
      durationMs: null
    }
  ];
}

function buildWorldPrompt(formState: GenesisFormState): string {
  const sections = [
    "你正在为一款同人冒险 RPG 执行创世编译。",
    "请严格根据以下三块输入生成世界设定、主线任务、开场章节，以及玩家角色的五维初始属性值。",
    `【什么世界，什么时间线】\n${formState.worldAndTimeline.trim() || "未填写，请根据其他信息合理补完。"}`,
    `【你想实现什么】\n${formState.playerGoal.trim() || "未填写，请根据世界背景推导一个明确可玩的主线目标。"}`,
    `【角色卡】\n${formState.characterCard.trim() || "未填写，请生成一名适合当前故事切入点的玩家角色。"}`
  ];

  return sections.join("\n\n").trim();
}

function getVisibleCharacterCount(formState: GenesisFormState): number {
  return [
    formState.worldAndTimeline,
    formState.playerGoal,
    formState.characterCard
  ].reduce(
    (total, section) =>
      total + Array.from(section).filter((char) => !/\s/.test(char)).length,
    0
  );
}

function getFilledSectionCount(formState: GenesisFormState): number {
  return Object.values(formState).filter((section) => section.trim().length > 0)
    .length;
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
  const [formState, setFormState] = useState<GenesisFormState>(INITIAL_FORM_STATE);
  const [stages, setStages] = useState<GenesisStage[]>(createInitialStages);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [lastTotalMs, setLastTotalMs] = useState<number | null>(null);
  const prompt = buildWorldPrompt(formState);
  const deferredPrompt = useDeferredValue(prompt);
  const visibleCharacterCount = getVisibleCharacterCount(formState);
  const filledSectionCount = getFilledSectionCount(formState);
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
        detail: "正在锚定时间线、生成主线任务，并根据角色卡分配开局属性。",
        status: "active",
        durationMs: null
      },
      {
        id: "game_start",
        label: "第一幕叙事",
        detail: "等待创世结果落地后启动。",
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
          detail: "世界、主线和角色属性已经编译完成，马上把你送入开场场景。",
          status: "done",
          durationMs: worldDurationMs
        },
        {
          id: "game_start",
          label: "第一幕叙事",
          detail: "正在生成序章，把当前世界的第一幕真正推开。",
          status: "active",
          durationMs: null
        }
      ]);

      const startStartedAt = performance.now();
      const startResponse = await startGame({
        world_config: worldResponse.world_config,
        world_prompt: trimmedPrompt,
        prologue_text: worldResponse.prologue_text ?? null
      });
      const openingDurationMs =
        startResponse.telemetry?.total_ms ??
        Math.round(performance.now() - startStartedAt);
      const totalMs = Math.round(performance.now() - flowStartedAt);

      setStages([
        {
          id: "world_generate",
          label: "世界织布机",
          detail: "世界、主线和角色属性已经编译完成，马上把你送入开场场景。",
          status: "done",
          durationMs: worldDurationMs
        },
        {
          id: "game_start",
          label: "第一幕叙事",
          detail: "序章已落地，第一幕已经加载完成。",
          status: "done",
          durationMs: openingDurationMs
        }
      ]);
      setLastTotalMs(totalMs);

      startTransition(() => {
        startSession(startResponse, trimmedPrompt);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "世界启动失败。";

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
        <h1 className="genesis-title">把你的创世设定拆成三块。</h1>
        <p className="genesis-copy">
          创世 AI 会先理解世界与时间线，再提炼你真正想推进的目标，最后根据角色卡给出开局主线和玩家属性。
        </p>

        <form className="genesis-form" onSubmit={handleSubmit}>
          <div className="genesis-prompt-grid">
            <label className="genesis-prompt-block">
              <span className="genesis-prompt-kicker">01 · 世界与时间线</span>
              <strong className="genesis-prompt-title">
                什么世界，什么时间线
              </strong>
              <span className="genesis-prompt-copy">
                写清作品、AU 设定、时间节点、关键角色是否还活着，以及世界现在处于什么局势。
              </span>
              <textarea
                className="genesis-textarea genesis-split-textarea"
                onChange={(event) =>
                  setFormState((current) => ({
                    ...current,
                    worldAndTimeline: event.target.value
                  }))
                }
                placeholder="例：咒术回战原作时间线，涩谷事变前一周，东京校与高专高层关系紧绷，宿傩容器身份已经公开。"
                rows={6}
                value={formState.worldAndTimeline}
              />
            </label>

            <label className="genesis-prompt-block">
              <span className="genesis-prompt-kicker">02 · 目标</span>
              <strong className="genesis-prompt-title">你想实现什么</strong>
              <span className="genesis-prompt-copy">
                写你想达成的结果、想改写的命运，或者想围绕哪条主线展开长期推进。
              </span>
              <textarea
                className="genesis-textarea genesis-split-textarea"
                onChange={(event) =>
                  setFormState((current) => ({
                    ...current,
                    playerGoal: event.target.value
                  }))
                }
                placeholder="例：我想阻止涩谷事变失控，尽量救下原作里本来会死的人，并查出高层谁在暗中推动局势。"
                rows={6}
                value={formState.playerGoal}
              />
            </label>

            <label className="genesis-prompt-block is-character-card">
              <span className="genesis-prompt-kicker">03 · 角色卡</span>
              <strong className="genesis-prompt-title">角色卡</strong>
              <span className="genesis-prompt-copy">
                写名字、身份、能力倾向、性格、秘密、关系网。创世 AI 会据此给出五维初始属性值。
              </span>
              <textarea
                className="genesis-textarea genesis-split-textarea"
                onChange={(event) =>
                  setFormState((current) => ({
                    ...current,
                    characterCard: event.target.value
                  }))
                }
                placeholder="例：神宫寺凛，17 岁转校术师，擅长感知结界与追踪残秽，近战较弱但临场判断强，表面冷静，实际非常怕再失去同伴。"
                rows={7}
                value={formState.characterCard}
              />
            </label>
          </div>

          <div className="genesis-footer">
            <span className="genesis-hint">
              {filledSectionCount > 0
                ? `已整理 ${visibleCharacterCount} 个字，覆盖 ${filledSectionCount}/3 个创世块。`
                : "三块信息越清楚，生成出来的世界、主线和角色属性就越稳。"}
            </span>
            <button
              className="genesis-submit"
              disabled={!deferredPrompt.trim() || filledSectionCount === 0 || isLoading}
              type="submit"
            >
              {isLoading ? "创世引擎正在编织中..." : "生成并进入世界"}
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
              现在可以清楚看到流程卡在创世编译还是开场叙事。第一阶段负责生成世界、主线与角色属性，第二阶段负责把你真正送进序章。
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
