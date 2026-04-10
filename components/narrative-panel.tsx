"use client";

import { ReactNode, useEffect, useRef } from "react";

import { TypewriterText } from "@/components/typewriter-text";
import type { StoryLog } from "@/lib/types";

type NarrativePanelProps = {
  storyLogs: StoryLog[];
  isLoading: boolean;
  children: ReactNode;
};

export function NarrativePanel({
  storyLogs,
  isLoading,
  children
}: NarrativePanelProps) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth"
    });
  }, [storyLogs.length]);

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) {
      return;
    }

    const observer = new MutationObserver(() => {
      bottomRef.current?.scrollIntoView({
        behavior: "auto",
        block: "end"
      });
    });

    observer.observe(node, {
      childList: true,
      subtree: true,
      characterData: true
    });

    return () => observer.disconnect();
  }, []);

  return (
    <section className="panel panel-narrative">
      <div className="panel-header narrative-header">
        <span className="panel-kicker">本回合</span>
        <h2>叙事剧场</h2>
      </div>

      <div className="narrative-scroller" ref={scrollerRef}>
        {storyLogs.length === 0 ? (
          <p className="empty-copy centered">舞台已经就绪，等待你的第一条行动。</p>
        ) : null}

        {storyLogs.map((entry) => (
          <article className={`story-line story-${entry.role}`} key={entry.id}>
            <span className="story-role">{entry.role === "user" ? "你" : "旁白"}</span>
            {entry.role === "system" ? (
              <TypewriterText
                text={entry.text}
                animate={entry.animate}
                className="story-text"
              />
            ) : (
              <div className="story-text">
                <p className="story-paragraph story-paragraph-user">{entry.text}</p>
              </div>
            )}
          </article>
        ))}
        <div className="narrative-bottom-anchor" ref={bottomRef} />
      </div>

      <div className="narrative-terminal">
        {isLoading ? <div className="narrative-loading">引擎正在结算规则与叙事…</div> : null}
        {children}
      </div>
    </section>
  );
}
