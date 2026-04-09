import {
  getEnvironmentHeading,
  getCurrentLocationLabel,
  getInventoryHeading,
  getInventoryItemLabel,
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
            <p className="empty-copy">当前随身栏里还没有可识别物品。</p>
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
        <div className="status-heading">{getEnvironmentHeading(gameState)}</div>
        <div className="environment-stack">
          <div className="environment-chip">{getCurrentLocationLabel(gameState)}</div>
          {gameState.active_encounter ? (
            <div className="environment-chip accent">
              {gameState.active_encounter}
            </div>
          ) : null}
          {gameState.world_config.initial_quests.map((quest) => (
            <div className="quest-line" key={quest}>
              {quest}
            </div>
          ))}
        </div>
      </section>
    </aside>
  );
}
