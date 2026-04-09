import type { GameState, WorldGlossary } from "@/lib/types";

export function getStatLabel(glossary: WorldGlossary, statKey: string): string {
  return glossary.stats[statKey] ?? statKey;
}

export function getInventoryHeading(glossary: WorldGlossary): string {
  const categories = Object.values(glossary.item_categories);
  return categories.length > 0 ? categories.join(" / ") : "物品栏";
}

export function getStatsHeading(glossary: WorldGlossary): string {
  const labels = Object.values(glossary.stats).slice(0, 2);
  return labels.length > 0 ? labels.join(" / ") : "状态";
}

export function getEnvironmentHeading(state: GameState): string {
  const locationLabel = getCurrentLocationLabel(state);
  return locationLabel
    ? `当前环境 · ${locationLabel}`
    : "当前环境";
}

export function getCurrentLocationLabel(state: GameState): string {
  return (
    state.world_config.topology.nodes[state.current_location_id]?.title ??
    state.world_config.starting_location ??
    state.current_location_id
  );
}

export function getInventoryItemLabel(
  glossary: WorldGlossary,
  itemKey: string,
  temporaryItems: Record<string, string> = {}
): string {
  if (temporaryItems[itemKey]) {
    return temporaryItems[itemKey];
  }

  const entries = Object.entries(glossary.item_categories).sort(
    ([leftKey], [rightKey]) => rightKey.length - leftKey.length
  );

  for (const [categoryKey, categoryLabel] of entries) {
    if (itemKey.startsWith(categoryKey)) {
      const suffix = itemKey.slice(categoryKey.length).replace(/^_+/, "");
      return suffix ? `${categoryLabel} ${suffix}` : categoryLabel;
    }
  }

  return itemKey;
}

export function getVitalsSummary(state: GameState): string {
  return Object.entries(state.player.stats)
    .slice(0, 2)
    .map(
      ([key, value]) =>
        `${getStatLabel(state.world_config.glossary, key)} ${value}`
    )
    .join(" · ");
}
