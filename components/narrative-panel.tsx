"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { motion } from "framer-motion";
import { type ReactNode, useEffect, useRef } from "react";

import { TypewriterText } from "@/components/typewriter-text";
import type { PendingStoryLog, StoryLog, TurnFailureState } from "@/lib/types";

type NarrativePanelProps = {
  storyLogs: StoryLog[];
  pendingStoryLog: PendingStoryLog | null;
  turnFailure: TurnFailureState | null;
  isLoading: boolean;
  isStreaming: boolean;
  retryDisabled: boolean;
  statusMessage: string | null;
  onRetryTurn?: () => Promise<void>;
  children: ReactNode;
};

type StoryEntry = PendingStoryLog | StoryLog;

type NarrativeEntry =
  | {
      kind: "story";
      item: StoryEntry;
    }
  | {
      kind: "failure";
      item: TurnFailureState;
    };

const AUTO_SCROLL_THRESHOLD_PX = 120;
const VIRTUALIZATION_THRESHOLD = 40;

function getNarrativeEntryKey(entry: NarrativeEntry, index: number): string {
  if (entry.kind === "failure") {
    return entry.item.id;
  }

  return entry.item.id || `story-${index}`;
}

export function NarrativePanel({
  storyLogs,
  pendingStoryLog,
  turnFailure,
  isLoading,
  isStreaming,
  retryDisabled,
  statusMessage,
  onRetryTurn,
  children
}: NarrativePanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const previousEntryCountRef = useRef(0);
  const entries: NarrativeEntry[] = [
    ...storyLogs.map((item) => ({ kind: "story" as const, item })),
    ...(pendingStoryLog ? [{ kind: "story" as const, item: pendingStoryLog }] : []),
    ...(turnFailure ? [{ kind: "failure" as const, item: turnFailure }] : [])
  ];
  const useVirtualizedList =
    !isStreaming && entries.length >= VIRTUALIZATION_THRESHOLD;

  const rowVirtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => scrollRef.current,
    getItemKey: (index) => getNarrativeEntryKey(entries[index], index),
    estimateSize: (index) => {
      const entry = entries[index];
      if (!entry) {
        return 180;
      }

      if (entry.kind === "failure") {
        return 220;
      }

      return entry.item.role === "user" ? 150 : 220;
    },
    overscan: 6,
    measureElement: (element) => element.getBoundingClientRect().height
  });

  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) {
      return;
    }

    const updateStickiness = () => {
      const distanceFromBottom =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      shouldStickToBottomRef.current =
        distanceFromBottom <= AUTO_SCROLL_THRESHOLD_PX;
    };

    updateStickiness();
    scroller.addEventListener("scroll", updateStickiness, { passive: true });

    return () => {
      scroller.removeEventListener("scroll", updateStickiness);
    };
  }, []);

  useEffect(() => {
    rowVirtualizer.measure();
  }, [pendingStoryLog?.text, turnFailure?.id, rowVirtualizer]);

  useEffect(() => {
    if (entries.length === 0) {
      previousEntryCountRef.current = 0;
      return;
    }

    const hasNewEntry = entries.length !== previousEntryCountRef.current;
    previousEntryCountRef.current = entries.length;

    if (!shouldStickToBottomRef.current) {
      return;
    }

    const scroller = scrollRef.current;
    if (!scroller) {
      return;
    }

    if (useVirtualizedList) {
      rowVirtualizer.scrollToIndex(entries.length - 1, {
        align: "end",
        behavior: hasNewEntry ? "smooth" : "auto"
      });
      return;
    }

    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: hasNewEntry ? "smooth" : "auto"
    });
  }, [
    entries.length,
    pendingStoryLog?.text,
    turnFailure?.id,
    rowVirtualizer,
    useVirtualizedList
  ]);

  return (
    <section className="panel panel-narrative">
      <div className="panel-header narrative-header">
        <span className="panel-kicker">当前回合</span>
        <h2>叙事剧场</h2>
      </div>

      <div className="narrative-scroller" ref={scrollRef}>
        {entries.length === 0 ? (
          <p className="empty-copy centered">
            舞台已经就绪。输入第一条指令，故事就会开始。
          </p>
        ) : useVirtualizedList ? (
          <div
            className="narrative-virtualizer"
            style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const entry = entries[virtualRow.index];
              if (!entry) {
                return null;
              }

              return (
                <div
                  className="story-row"
                  data-index={virtualRow.index}
                  key={getNarrativeEntryKey(entry, virtualRow.index)}
                  ref={rowVirtualizer.measureElement}
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  {entry.kind === "failure" ? (
                    <RetryBubble
                      failure={entry.item}
                      onRetryTurn={onRetryTurn}
                      retryDisabled={retryDisabled}
                    />
                  ) : (
                    <StoryBubble entry={entry.item} />
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="narrative-stack">
            {entries.map((entry, index) =>
              entry.kind === "failure" ? (
                <div
                  className="story-row-inline"
                  key={getNarrativeEntryKey(entry, index)}
                >
                  <RetryBubble
                    failure={entry.item}
                    onRetryTurn={onRetryTurn}
                    retryDisabled={retryDisabled}
                  />
                </div>
              ) : (
                <div
                  className="story-row-inline"
                  key={getNarrativeEntryKey(entry, index)}
                >
                  <StoryBubble entry={entry.item} />
                </div>
              )
            )}
          </div>
        )}
      </div>

      <div className="narrative-terminal">
        {isLoading ? (
          <div className="narrative-loading">
            {isStreaming
              ? statusMessage ?? "旁白正在流式生成..."
              : "引擎正在结算规则与结果..."}
          </div>
        ) : null}
        {turnFailure ? (
          <TerminalRetryNotice
            failure={turnFailure}
            onRetryTurn={onRetryTurn}
            retryDisabled={retryDisabled}
          />
        ) : null}
        {children}
      </div>
    </section>
  );
}

function StoryBubble({ entry }: { entry: StoryEntry }) {
  const isStreamingEntry = "isStreaming" in entry && entry.isStreaming;

  return (
    <motion.article
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className={`story-line story-${entry.role}`}
      initial={
        entry.role === "system" &&
        (("animate" in entry && Boolean(entry.animate)) || isStreamingEntry)
          ? { opacity: 0, y: 18, scale: 0.985 }
          : false
      }
      transition={{
        type: "spring",
        stiffness: 280,
        damping: 24,
        mass: 0.82
      }}
    >
      <span className="story-role">
        {entry.role === "user" ? "玩家" : "系统"}
      </span>
      {entry.role === "system" ? (
        <TypewriterText
          text={entry.text}
          animate={"animate" in entry ? entry.animate : false}
          className="story-text"
          streaming={isStreamingEntry}
        />
      ) : (
        <div className="story-text">
          <p className="story-paragraph story-paragraph-user">{entry.text}</p>
        </div>
      )}
    </motion.article>
  );
}

function RetryBubble({
  failure,
  retryDisabled,
  onRetryTurn
}: {
  failure: TurnFailureState;
  retryDisabled: boolean;
  onRetryTurn?: () => Promise<void>;
}) {
  return (
    <motion.article
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className="story-line story-system story-retry"
      initial={{ opacity: 0, y: 18, scale: 0.985 }}
      transition={{
        type: "spring",
        stiffness: 260,
        damping: 24,
        mass: 0.86
      }}
    >
      <span className="story-role">系统</span>
      <div className="story-retry-copy">
        <p className="story-retry-title">本回合未能完整结算</p>
        <p className="story-retry-message">{failure.message}</p>
        <p className="story-retry-command">{`> ${failure.command}`}</p>
        <p className="story-retry-hint">
          {failure.retryable
            ? "可以直接重新尝试本回合，系统会复用上一条指令。"
            : "这次错误暂不支持自动重试，请检查服务状态后再继续。"}
        </p>
      </div>
      {failure.retryable && onRetryTurn ? (
        <div className="story-retry-actions">
          <button
            className="secondary-action"
            disabled={retryDisabled}
            onClick={() => {
              void onRetryTurn();
            }}
            type="button"
          >
            {retryDisabled ? "暂时无法重试" : "重新尝试本回合"}
          </button>
        </div>
      ) : null}
    </motion.article>
  );
}

function TerminalRetryNotice({
  failure,
  retryDisabled,
  onRetryTurn
}: {
  failure: TurnFailureState;
  retryDisabled: boolean;
  onRetryTurn?: () => Promise<void>;
}) {
  return (
    <div className="terminal-retry-banner" role="status">
      <div className="terminal-retry-copy">
        <p className="terminal-retry-title">本回合未能完整结算</p>
        <p className="terminal-retry-message">{failure.message}</p>
        <p className="terminal-retry-command">{`> ${failure.command}`}</p>
      </div>
      {failure.retryable && onRetryTurn ? (
        <button
          className="secondary-action"
          disabled={retryDisabled}
          onClick={() => {
            void onRetryTurn();
          }}
          type="button"
        >
          {retryDisabled ? "暂时无法重试" : "重新尝试本回合"}
        </button>
      ) : (
        <p className="terminal-retry-hint">
          这次错误暂不支持自动重试，请检查服务状态后再继续。
        </p>
      )}
    </div>
  );
}
