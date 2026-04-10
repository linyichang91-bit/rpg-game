import type {
  EncounterState,
  GameState,
  QuestState,
  WorldGlossary
} from "@/lib/types";

export interface QuestDisplayEntry {
  id: string;
  title: string;
  status: QuestState["status"];
  summary: string | null;
  progress: number;
  legacy: boolean;
}

export interface EncounterDisplayEntry {
  id: string;
  label: string;
  status: EncounterState["status"];
  locationLabel: string;
  enemyIds: string[];
  summary: string | null;
  legacy: boolean;
}

export function getStatLabel(glossary: WorldGlossary, statKey: string): string {
  return glossary.stats[statKey] ?? statKey;
}

export function getInventoryHeading(glossary: WorldGlossary): string {
  const categories = Object.values(glossary.item_categories);
  if (categories.length === 0) {
    return "背包";
  }
  return `背包 - ${categories.slice(0, 2).join(" / ")}`;
}

export function getStatsHeading(glossary: WorldGlossary): string {
  const labels = Object.values(glossary.stats).slice(0, 2);
  return labels.length > 0 ? `状态 - ${labels.join(" / ")}` : "状态";
}

export function getEnvironmentHeading(state: GameState): string {
  const locationLabel = getCurrentLocationLabel(state);
  return locationLabel ? `环境 - ${locationLabel}` : "环境";
}

export function getQuestHeading(state: GameState): string {
  const questCount = getQuestEntries(state).length;
  return questCount > 0 ? `任务 - ${questCount}` : "任务";
}

export function getEncounterHeading(state: GameState): string {
  const encounterCount = getEncounterEntries(state).length;
  return encounterCount > 0 ? `遭遇记录 - ${encounterCount}` : "遭遇记录";
}

export function getCurrentLocationLabel(state: GameState): string {
  return (
    state.world_config.topology.nodes[state.current_location_id]?.title ??
    state.world_config.starting_location ??
    state.current_location_id
  );
}

export function getVitalsSummary(state: GameState): string {
  return Object.entries(state.player.stats)
    .slice(0, 2)
    .map(
      ([key, value]) =>
        `${getStatLabel(state.world_config.glossary, key)} ${value}`
    )
    .join(" / ");
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

export function getQuestEntries(state: GameState): QuestDisplayEntry[] {
  const runtimeEntries = state.quest_log ? Object.entries(state.quest_log) : [];
  if (runtimeEntries.length > 0) {
    return runtimeEntries
      .map(([id, quest]) => ({
        id,
        title: normalizeQuestTitle(quest.title, id),
        status: quest.status,
        summary: normalizeQuestSummary(quest.summary),
        progress: quest.progress ?? 0,
        legacy: false
      }))
      .sort(sortQuestEntries);
  }

  return state.world_config.initial_quests.map((quest, index) => ({
    id: `legacy_quest_${index}`,
    title: quest,
    status: "active",
    summary: null,
    progress: 0,
    legacy: true
  }));
}

export function getEncounterEntries(state: GameState): EncounterDisplayEntry[] {
  const runtimeEntries = state.encounter_log
    ? Object.entries(state.encounter_log)
    : [];
  if (runtimeEntries.length > 0) {
    return runtimeEntries
      .map(([id, encounter]) => ({
        id,
        label: normalizeEncounterLabel(encounter.label, id),
        status: encounter.status,
        locationLabel:
          state.world_config.topology.nodes[encounter.location_id]?.title ??
          encounter.location_id,
        enemyIds: encounter.enemy_ids ?? [],
        summary: encounter.summary ?? null,
        legacy: false
      }))
      .sort(sortEncounterEntries);
  }

  const legacyEnemyIds = Object.keys(state.encounter_entities ?? {});
  if (state.active_encounter || legacyEnemyIds.length > 0) {
    return [
      {
        id: state.active_encounter ?? "legacy_encounter",
        label: normalizeEncounterLabel(state.active_encounter, "legacy_encounter"),
        status: legacyEnemyIds.length > 0 ? "active" : "resolved",
        locationLabel: getCurrentLocationLabel(state),
        enemyIds: legacyEnemyIds,
        summary:
          legacyEnemyIds.length > 0
            ? `当前有 ${legacyEnemyIds.length} 名敌对单位`
            : "该遭遇已结束",
        legacy: true
      }
    ];
  }

  return [];
}

export function getQuestStatusLabel(status: QuestState["status"]): string {
  switch (status) {
    case "completed":
      return "已完成";
    case "failed":
      return "已失败";
    default:
      return "进行中";
  }
}

export function getEncounterStatusLabel(
  status: EncounterState["status"]
): string {
  switch (status) {
    case "resolved":
      return "已解决";
    case "escaped":
      return "已脱离";
    default:
      return "进行中";
  }
}

export function getQuestProgressLabel(quest: QuestDisplayEntry): string {
  if (quest.status !== "active") {
    return getQuestStatusLabel(quest.status);
  }
  return `进度 ${Math.max(0, quest.progress)}`;
}

export function getEncounterSummary(entry: EncounterDisplayEntry): string {
  const enemySummary =
    entry.enemyIds.length > 0
      ? `敌对单位 ${entry.enemyIds.length}`
      : "无敌对单位";
  return entry.summary
    ? `${entry.locationLabel} · ${enemySummary} · ${entry.summary}`
    : `${entry.locationLabel} · ${enemySummary}`;
}

export function getQuestSummary(quest: QuestDisplayEntry): string {
  const progress = getQuestProgressLabel(quest);
  return quest.summary ? `${progress} · ${quest.summary}` : progress;
}

function sortQuestEntries(
  left: QuestDisplayEntry,
  right: QuestDisplayEntry
): number {
  return (
    questStatusPriority(left.status) - questStatusPriority(right.status) ||
    right.progress - left.progress ||
    left.title.localeCompare(right.title)
  );
}

function sortEncounterEntries(
  left: EncounterDisplayEntry,
  right: EncounterDisplayEntry
): number {
  return (
    encounterStatusPriority(left.status) - encounterStatusPriority(right.status) ||
    left.label.localeCompare(right.label)
  );
}

function questStatusPriority(status: QuestState["status"]): number {
  switch (status) {
    case "active":
      return 0;
    case "completed":
      return 1;
    case "failed":
      return 2;
    default:
      return 3;
  }
}

function encounterStatusPriority(status: EncounterState["status"]): number {
  switch (status) {
    case "active":
      return 0;
    case "resolved":
      return 1;
    case "escaped":
      return 2;
    default:
      return 3;
  }
}

function normalizeQuestTitle(rawTitle: string, fallbackId: string): string {
  const title = rawTitle?.trim();
  if (title) {
    return title;
  }
  return formatQuestFallbackTitle(fallbackId);
}

function normalizeQuestSummary(rawSummary: string | null | undefined): string | null {
  const summary = rawSummary?.trim();
  if (!summary) {
    return null;
  }
  if (summary.startsWith("Opening objective tied to ")) {
    const location = summary.replace("Opening objective tied to ", "").replace(/\.$/, "");
    return `开场目标，地点：${location}`;
  }
  if (summary === "A new objective has entered the scene.") {
    return "新的目标已出现。";
  }
  return summary;
}

function normalizeEncounterLabel(rawLabel: string | null | undefined, fallbackId: string): string {
  const label = rawLabel?.trim();
  if (label) {
    return label;
  }
  return formatEncounterFallbackTitle(fallbackId);
}

function formatQuestFallbackTitle(questId: string): string {
  const match = /^quest_(\d+)$/.exec(questId);
  if (!match) {
    return "未命名任务";
  }
  const index = Number.parseInt(match[1], 10);
  if (Number.isNaN(index)) {
    return "未命名任务";
  }
  return `任务 ${index}`;
}

function formatEncounterFallbackTitle(encounterId: string): string {
  const match = /^encounter_(\d+|[a-z_]+)$/.exec(encounterId);
  if (!match) {
    return "当前遭遇";
  }
  const suffix = match[1];
  if (/^\d+$/.test(suffix)) {
    return `遭遇 ${Number.parseInt(suffix, 10)}`;
  }
  if (suffix === "opening") {
    return "开场遭遇";
  }
  return "当前遭遇";
}
