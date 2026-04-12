"use client";

import { useEffect, useRef, useState } from "react";

import { AnimatePresence, animate, motion } from "framer-motion";

import {
  getChapterTensionLabel,
  getCoreAttributeEntries,
  getCoreAttributesHeading,
  getCurrentChapterSummary,
  getCurrentChapterTitle,
  getCurrentLocationLabel,
  getEncounterEntries,
  getEncounterHeading,
  getEncounterStatusLabel,
  getEncounterSummary,
  getEnvironmentHeading,
  getInventoryHeading,
  getInventoryItemLabel,
  getMainQuestSummary,
  getMainQuestTitle,
  getPowerLevelDisplay,
  getQuestEntries,
  getQuestHeading,
  getQuestStatusLabel,
  getQuestSummary,
  getStatLabel,
  getStatsHeading,
  getStoryMilestoneStatusLabel,
  getStoryMilestoneSummary,
  getStoryProgressHeading,
  getStoryProgressSummary
} from "@/lib/format";
import type { GameState } from "@/lib/types";

type StatusPanelProps = {
  gameState: GameState;
};

const rowSpring = {
  type: "spring" as const,
  stiffness: 360,
  damping: 28,
  mass: 0.75
};

function AnimatedNumber({ value }: { value: number }) {
  const [displayValue, setDisplayValue] = useState(value);
  const previousValueRef = useRef(value);

  useEffect(() => {
    const controls = animate(previousValueRef.current, value, {
      duration: 0.45,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (latest) => {
        setDisplayValue(Math.round(latest));
      }
    });

    previousValueRef.current = value;
    return () => controls.stop();
  }, [value]);

  return (
    <motion.strong
      animate={{ scale: [1, 1.08, 1], y: [0, -2, 0] }}
      className="stat-value"
      key={value}
      transition={{ duration: 0.4, ease: "easeOut" }}
    >
      {displayValue}
    </motion.strong>
  );
}

export function StatusPanel({ gameState }: StatusPanelProps) {
  const { glossary } = gameState.world_config;
  const campaignContext = gameState.world_config.world_book.campaign_context;
  const powerScaling = gameState.world_config.world_book.power_scaling ?? null;
  const statsEntries = Object.entries(gameState.player.stats);
  const powerDisplay = getPowerLevelDisplay(gameState);
  const coreAttributeEntries = getCoreAttributeEntries(gameState);
  const inventoryEntries = Object.entries(gameState.player.inventory);
  const questEntries = getQuestEntries(gameState);
  const encounterEntries = getEncounterEntries(gameState);
  const activeEncounter = encounterEntries.find(
    (encounter) => encounter.id === gameState.active_encounter
  );
  const milestones = campaignContext?.milestones ?? [];
  const growth = gameState.player.growth ?? null;

  return (
    <aside className="panel panel-status">
      <div className="panel-header">
        <span className="panel-kicker">
          {gameState.world_config.fanfic_meta.base_ip}
        </span>
        <h2>{gameState.world_config.fanfic_meta.universe_type}</h2>
      </div>

      <section className="status-section">
        <div className="status-heading">{getStoryProgressHeading()}</div>
        <p className="empty-copy">{getStoryProgressSummary(campaignContext)}</p>
        <div className="environment-stack">
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="quest-line stacked"
            initial={{ opacity: 0, y: 12 }}
            layout
            transition={rowSpring}
          >
            <div className="entry-main">
              <strong>{getMainQuestTitle(campaignContext)}</strong>
              <span>{getMainQuestSummary(campaignContext)}</span>
            </div>
            <span className="status-pill status-active">
              {powerScaling?.scale_label ?? "主线"}
            </span>
          </motion.div>

          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="quest-line stacked"
            initial={{ opacity: 0, y: 12 }}
            layout
            transition={rowSpring}
          >
            <div className="entry-main">
              <strong>{getCurrentChapterTitle(campaignContext)}</strong>
              <span>{getCurrentChapterSummary(campaignContext)}</span>
            </div>
            <span className="status-pill status-active">
              {campaignContext?.current_chapter
                ? `紧迫度 ${getChapterTensionLabel(
                    campaignContext.current_chapter.tension_level
                  )}`
                : "待生成"}
            </span>
          </motion.div>

          {growth ? (
            <motion.div
              animate={{ opacity: 1, y: 0 }}
              className="environment-chip accent"
              initial={{ opacity: 0, y: 8 }}
              layout
              transition={rowSpring}
            >
              {`成长 Lv ${growth.level} · XP ${growth.xp} · 熟练加值 +${growth.proficiency_bonus}`}
            </motion.div>
          ) : null}

          <AnimatePresence initial={false} mode="popLayout">
            {milestones.length === 0 ? (
              <motion.p
                animate={{ opacity: 1, y: 0 }}
                className="empty-copy"
                initial={{ opacity: 0, y: 10 }}
                key="milestone-empty"
                layout
                transition={rowSpring}
              >
                当前章节暂时没有里程碑。
              </motion.p>
            ) : (
              milestones.map((milestone) => (
                <motion.div
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  className="quest-line stacked"
                  exit={{ opacity: 0, y: -10, scale: 0.98 }}
                  initial={{ opacity: 0, y: 14, scale: 0.98 }}
                  key={milestone.milestone_id}
                  layout
                  transition={rowSpring}
                >
                  <div className="entry-main">
                    <strong>{milestone.title}</strong>
                    <span>{getStoryMilestoneSummary(milestone)}</span>
                  </div>
                  <span
                    className={`status-pill ${
                      milestone.is_completed
                        ? "status-completed"
                        : "status-active"
                    }`}
                  >
                    {getStoryMilestoneStatusLabel(milestone)}
                  </span>
                </motion.div>
              ))
            )}
          </AnimatePresence>
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getStatsHeading(glossary)}</div>
        <div className="stat-grid">
          {statsEntries.map(([key, value]) => (
            <motion.article
              animate={{ opacity: 1, y: 0 }}
              className="stat-card"
              initial={{ opacity: 0, y: 8 }}
              key={key}
              layout
              transition={rowSpring}
            >
              <span className="stat-label">{getStatLabel(glossary, key)}</span>
              <AnimatedNumber value={value} />
            </motion.article>
          ))}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">修为 · {powerDisplay.rankLabel}</div>
        <div className="stat-grid">
          <motion.article
            animate={{ opacity: 1, y: 0 }}
            className="stat-card accent"
            initial={{ opacity: 0, y: 8 }}
            key="power_level"
            layout
            transition={rowSpring}
          >
            <span className="stat-label">战斗力</span>
            <AnimatedNumber value={powerDisplay.powerLevel} />
          </motion.article>
          <motion.article
            animate={{ opacity: 1, y: 0 }}
            className="stat-card"
            initial={{ opacity: 0, y: 8 }}
            key="rank_label"
            layout
            transition={rowSpring}
          >
            <span className="stat-label">修为</span>
            <motion.strong
              animate={{ scale: [1, 1.08, 1], y: [0, -2, 0] }}
              className="stat-value"
              key={powerDisplay.rankLabel}
              transition={{ duration: 0.4, ease: "easeOut" }}
            >
              {powerDisplay.rankLabel}
            </motion.strong>
          </motion.article>
        </div>
        <div className="attribute-qualitative">
          {coreAttributeEntries.map((entry) => (
            <span className="attribute-tag" key={entry.key}>
              {entry.label} {getAttributeQualitative(entry.value)}
            </span>
          ))}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getInventoryHeading(glossary)}</div>
        <div className="inventory-list">
          {inventoryEntries.length === 0 ? (
            <p className="empty-copy">当前没有可识别物品。</p>
          ) : (
            <AnimatePresence initial={false} mode="popLayout">
              {inventoryEntries.map(([key, value]) => (
                <motion.div
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  className="inventory-row"
                  exit={{ opacity: 0, y: -10, scale: 0.98 }}
                  initial={{ opacity: 0, y: 12, scale: 0.98 }}
                  key={key}
                  layout
                  transition={rowSpring}
                >
                  <span>
                    {getInventoryItemLabel(
                      glossary,
                      key,
                      gameState.player.temporary_items
                    )}
                  </span>
                  <strong>x{value}</strong>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getQuestHeading(gameState)}</div>
        <div className="environment-stack">
          {questEntries.length === 0 ? (
            <p className="empty-copy">当前还没有任务记录。</p>
          ) : (
            <AnimatePresence initial={false} mode="popLayout">
              {questEntries.map((quest) => (
                <motion.div
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  className="quest-line stacked"
                  exit={{ opacity: 0, y: -10, scale: 0.98 }}
                  initial={{ opacity: 0, y: 14, scale: 0.98 }}
                  key={quest.id}
                  layout
                  transition={rowSpring}
                >
                  <div className="entry-main">
                    <strong>{quest.title}</strong>
                    <span>{getQuestSummary(quest)}</span>
                  </div>
                  <span className={`status-pill status-${quest.status}`}>
                    {getQuestStatusLabel(quest.status)}
                  </span>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getEncounterHeading(gameState)}</div>
        <div className="environment-stack">
          {encounterEntries.length === 0 ? (
            <p className="empty-copy">当前没有遭遇记录。</p>
          ) : (
            <AnimatePresence initial={false} mode="popLayout">
              {encounterEntries.map((encounter) => (
                <motion.div
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  className="quest-line stacked"
                  exit={{ opacity: 0, y: -10, scale: 0.98 }}
                  initial={{ opacity: 0, y: 14, scale: 0.98 }}
                  key={encounter.id}
                  layout
                  transition={rowSpring}
                >
                  <div className="entry-main">
                    <strong>{encounter.label}</strong>
                    <span>{getEncounterSummary(encounter)}</span>
                  </div>
                  <span className={`status-pill status-${encounter.status}`}>
                    {getEncounterStatusLabel(encounter.status)}
                  </span>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getEnvironmentHeading(gameState)}</div>
        <div className="environment-stack">
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="environment-chip"
            initial={{ opacity: 0, y: 8 }}
            layout
            transition={rowSpring}
          >
            {getCurrentLocationLabel(gameState)}
          </motion.div>
          <AnimatePresence initial={false}>
            {activeEncounter ? (
              <motion.div
                animate={{ opacity: 1, y: 0, scale: 1 }}
                className="environment-chip accent"
                exit={{ opacity: 0, y: -8, scale: 0.98 }}
                initial={{ opacity: 0, y: 12, scale: 0.98 }}
                key={activeEncounter.id}
                layout
                transition={rowSpring}
              >
                {`${activeEncounter.label} · ${getEncounterStatusLabel(
                  activeEncounter.status
                )}`}
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </section>
    </aside>
  );
}

function getAttributeQualitative(value: number): string {
  if (value >= 18) return "极强";
  if (value >= 15) return "强";
  if (value >= 12) return "良";
  if (value >= 9) return "平";
  if (value >= 6) return "弱";
  return "极弱";
}
