"use client";

import { FormEvent, startTransition, useDeferredValue, useState } from "react";

import { generateWorld, startGame } from "@/lib/api";
import { useSandboxStore } from "@/lib/store";

export function GenesisView() {
  const [prompt, setPrompt] = useState("");
  const deferredPrompt = useDeferredValue(prompt);
  const setLoading = useSandboxStore((state) => state.setLoading);
  const setError = useSandboxStore((state) => state.setError);
  const setWorldPrompt = useSandboxStore((state) => state.setWorldPrompt);
  const startSession = useSandboxStore((state) => state.startSession);
  const isLoading = useSandboxStore((state) => state.isLoading);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isLoading) {
      return;
    }

    setLoading(true);
    setError(null);
    setWorldPrompt(trimmedPrompt);

    try {
      const worldResponse = await generateWorld(trimmedPrompt);
      const startResponse = await startGame({
        world_config: worldResponse.world_config,
        world_prompt: trimmedPrompt
      });

      startTransition(() => {
        startSession(startResponse, trimmedPrompt);
      });
    } catch (error) {
      setError(error instanceof Error ? error.message : "世界启动失败。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="genesis-shell">
      <div className="genesis-card">
        <div className="panel-kicker">世界织布机</div>
        <h1 className="genesis-title">描述你想投身其中的同人世界。</h1>
        <p className="genesis-copy">
          写下一个 AU、跨界脑洞，或原创设定。引擎会先编织规则，再让第一段
          剧情真正落地。
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
                : "一个好提示通常会写清原著 IP、魔改点，以及主角切入口。"}
            </span>
            <button
              className="genesis-submit"
              disabled={!deferredPrompt.trim() || isLoading}
              type="submit"
            >
              {isLoading
                ? "世界织布机正在编织物理法则……"
                : "生成并进入世界"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
