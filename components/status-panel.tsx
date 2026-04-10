import {
  getCurrentLocationLabel,
  getEncounterEntries,
  getEncounterHeading,
  getEncounterStatusLabel,
  getEncounterSummary,
  getEnvironmentHeading,
  getInventoryHeading,
  getInventoryItemLabel,
  getQuestEntries,
  getQuestHeading,
  getQuestStatusLabel,
  getQuestSummary,
  getStatLabel,
  getStatsHeading
} from "@/lib/format";
import type { GameState } from "@/lib/types";

type StatusPanelProps = {
  gameState: GameState;
};

export function StatusPanel({ gameState }: StatusPanelProps) {
  const { glossary } = gameState.world_config;
  const statsEntries = Object.entries(gameState.player.stats);
  const inventoryEntries = Object.entries(gameState.player.inventory);
  const questEntries = getQuestEntries(gameState);
  const encounterEntries = getEncounterEntries(gameState);
  const activeEncounter = encounterEntries.find(
    (encounter) => encounter.id === gameState.active_encounter
  );

  return (
    <aside className="panel panel-status">
      <div className="panel-header">
        <span className="panel-kicker">
          {gameState.world_config.fanfic_meta.base_ip}
        </span>
        <h2>{gameState.world_config.fanfic_meta.universe_type}</h2>
      </div>

      <section className="status-section">
        <div className="status-heading">{getStatsHeading(glossary)}</div>
        <div className="stat-grid">
          {statsEntries.map(([key, value]) => (
            <article className="stat-card" key={key}>
              <span className="stat-label">{getStatLabel(glossary, key)}</span>
              <strong className="stat-value">{value}</strong>
            </article>
          ))}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getInventoryHeading(glossary)}</div>
        <div className="inventory-list">
          {inventoryEntries.length === 0 ? (
            <p className="empty-copy">当前没有可识别物品。</p>
          ) : (
            inventoryEntries.map(([key, value]) => (
              <div className="inventory-row" key={key}>
                <span>
                  {getInventoryItemLabel(
                    glossary,
                    key,
                    gameState.player.temporary_items
                  )}
                </span>
                <strong>x{value}</strong>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getQuestHeading(gameState)}</div>
        <div className="environment-stack">
          {questEntries.length === 0 ? (
            <p className="empty-copy">当前还没有任务记录。</p>
          ) : (
            questEntries.map((quest) => (
              <div className="quest-line stacked" key={quest.id}>
                <div className="entry-main">
                  <strong>{quest.title}</strong>
                  <span>{getQuestSummary(quest)}</span>
                </div>
                <span className={`status-pill status-${quest.status}`}>
                  {getQuestStatusLabel(quest.status)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getEncounterHeading(gameState)}</div>
        <div className="environment-stack">
          {encounterEntries.length === 0 ? (
            <p className="empty-copy">当前没有遭遇记录。</p>
          ) : (
            encounterEntries.map((encounter) => (
              <div className="quest-line stacked" key={encounter.id}>
                <div className="entry-main">
                  <strong>{encounter.label}</strong>
                  <span>{getEncounterSummary(encounter)}</span>
                </div>
                <span className={`status-pill status-${encounter.status}`}>
                  {getEncounterStatusLabel(encounter.status)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="status-section">
        <div className="status-heading">{getEnvironmentHeading(gameState)}</div>
        <div className="environment-stack">
          <div className="environment-chip">
            {getCurrentLocationLabel(gameState)}
          </div>
          {activeEncounter ? (
            <div className="environment-chip accent">
              {`${activeEncounter.label} · ${getEncounterStatusLabel(activeEncounter.status)}`}
            </div>
          ) : null}
        </div>
      </section>
    </aside>
  );
}
