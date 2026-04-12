import type {
  CampaignContext,
  EncounterState,
  GameState,
  QuestState,
  StoryMilestone,
  WorldGlossary
} from "@/lib/types";

export interface QuestDisplayEntry {
  id: string;
  title: string;
  status: QuestState["status"];
  summary: string | null;
  progress: number;
  legacy: boolean;
  mainline?: boolean;
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

export interface CoreAttributeEntry {
  key: string;
  label: string;
  value: number;
}

const CORE_ATTRIBUTE_KEYS = [
  "stat_power",
  "stat_agility",
  "stat_insight",
  "stat_tenacity",
  "stat_presence"
] as const;

const CORE_ATTRIBUTE_FALLBACK_LABELS: Record<string, string> = {
  stat_power: "力量",
  stat_agility: "敏捷",
  stat_insight: "洞察",
  stat_tenacity: "韧性",
  stat_presence: "魅力"
};

const CHAPTER_TENSION_LABELS: Record<number, string> = {
  1: "平稳",
  2: "微紧",
  3: "紧绷",
  4: "危急",
  5: "决战"
};

export function getStatLabel(glossary: WorldGlossary, statKey: string): string {
  return glossary.stats[statKey] ?? statKey;
}

export function getCoreAttributeLabel(
  glossary: WorldGlossary,
  attributeKey: string
): string {
  return (
    glossary.attributes?.[attributeKey] ??
    CORE_ATTRIBUTE_FALLBACK_LABELS[attributeKey] ??
    attributeKey
  );
}

export function getCoreAttributeEntries(state: GameState): CoreAttributeEntry[] {
  return CORE_ATTRIBUTE_KEYS.map((key) => ({
    key,
    label: getCoreAttributeLabel(state.world_config.glossary, key),
    value: state.player.attributes?.[key] ?? 0
  }));
}

export function getCoreAttributesHeading(glossary: WorldGlossary): string {
  const labels = CORE_ATTRIBUTE_KEYS.map((key) =>
    getCoreAttributeLabel(glossary, key)
  ).filter(Boolean);
  return labels.length > 0
    ? `通用属性 · ${labels.slice(0, 2).join(" / ")}`
    : "通用属性";
}

export function getPowerLevelDisplay(state: GameState): {
  powerLevel: number;
  rankLabel: string;
} {
  return {
    powerLevel: state.player.power_level ?? 0,
    rankLabel: state.player.rank_label ?? "未定级"
  };
}

export function getInventoryHeading(glossary: WorldGlossary): string {
  const categories = Object.values(glossary.item_categories);
  if (categories.length === 0) {
    return "背包";
  }
  return `背包 · ${categories.slice(0, 2).join(" / ")}`;
}

export function getStatsHeading(glossary: WorldGlossary): string {
  const labels = Object.values(glossary.stats).slice(0, 2);
  return labels.length > 0 ? `状态 · ${labels.join(" / ")}` : "状态";
}

export function getStoryProgressHeading(): string {
  return "主线进度";
}

export function getStoryProgressSummary(
  context: CampaignContext | null | undefined
): string {
  if (!context) {
    return "等待 Weaver 根据世界观生成主线与章节。";
  }

  const milestones = context.milestones ?? [];
  const completedCount = milestones.filter((milestone) => milestone.is_completed)
    .length;
  const chapterProgress = getCurrentChapterProgressSummary(context);
  const pieces = [
    milestones.length > 0 ? `里程碑 ${completedCount}/${milestones.length}` : null,
    chapterProgress
  ].filter(Boolean);

  if (pieces.length === 0) {
    return "主线已就位，等待章节细化。";
  }

  return pieces.join(" · ");
}

export function getMainQuestTitle(
  context: CampaignContext | null | undefined
): string {
  const title = context?.main_quest?.title?.trim();
  return title || "主线尚未生成";
}

export function getMainQuestSummary(
  context: CampaignContext | null | undefined
): string {
  const mainQuest = context?.main_quest;
  if (!mainQuest) {
    return "等待 Weaver 根据世界观生成最终目标。";
  }

  const pieces = [
    mainQuest.final_goal?.trim(),
    mainQuest.summary?.trim()
  ].filter(Boolean);

  if (pieces.length === 0) {
    return "主线目标已建立，等待进一步展开。";
  }

  return pieces.join(" · ");
}

export function getCurrentChapterTitle(
  context: CampaignContext | null | undefined
): string {
  const title = context?.current_chapter?.title?.trim();
  return title || "当前章节";
}

export function getChapterTensionLabel(level: number | null | undefined): string {
  const normalizedLevel = clamp(Math.round(level ?? 0), 1, 5);
  return `${normalizedLevel}/5 ${CHAPTER_TENSION_LABELS[normalizedLevel]}`;
}

export function getCurrentChapterProgressSummary(
  context: CampaignContext | null | undefined
): string | null {
  const chapter = context?.current_chapter;
  if (!chapter) {
    return null;
  }

  const progress = chapter.progress_percent;
  if (typeof progress !== "number" || Number.isNaN(progress)) {
    return null;
  }

  return `章节进度 ${clamp(Math.round(progress), 0, 100)}%`;
}

export function getCurrentChapterSummary(
  context: CampaignContext | null | undefined
): string {
  const chapter = context?.current_chapter;
  if (!chapter) {
    return "尚未生成章节任务。";
  }

  const pieces = [
    chapter.objective?.trim() || "等待章节目标。",
    `紧迫度 ${getChapterTensionLabel(chapter.tension_level)}`
  ];
  const progress = getCurrentChapterProgressSummary(context);
  if (progress) {
    pieces.push(progress);
  }

  return pieces.join(" · ");
}

export function getStoryMilestoneStatusLabel(
  milestone: StoryMilestone
): string {
  return milestone.is_completed ? "已达成" : "进行中";
}

export function getStoryMilestoneSummary(
  milestone: StoryMilestone
): string {
  const summary = milestone.summary?.trim();
  if (summary) {
    return summary;
  }
  return milestone.is_completed ? "该里程碑已完成。" : "等待推进。";
}

export function getEnvironmentHeading(state: GameState): string {
  const locationLabel = getCurrentLocationLabel(state);
  return locationLabel ? `环境 · ${locationLabel}` : "环境";
}

export function getQuestHeading(state: GameState): string {
  const questCount = getQuestEntries(state).length;
  return questCount > 0 ? `任务 · ${questCount}` : "任务";
}

export function getEncounterHeading(state: GameState): string {
  const encounterCount = getEncounterEntries(state).length;
  return encounterCount > 0 ? `遭遇 · ${encounterCount}` : "遭遇";
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
    return injectMainQuestEntry(
      state,
      runtimeEntries
        .map(([id, quest]) => ({
          id,
          title: normalizeQuestTitle(quest.title, id),
          status: quest.status,
          summary: normalizeQuestSummary(quest.summary),
          progress: quest.progress ?? 0,
          legacy: false
        }))
    ).sort(sortQuestEntries);
  }

  return injectMainQuestEntry(
    state,
    state.world_config.initial_quests.map((quest, index) => ({
      id: `legacy_quest_${index}`,
      title: quest,
      status: "active",
      summary: null,
      progress: 0,
      legacy: true
    }))
  ).sort(sortQuestEntries);
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
        label: normalizeEncounterLabel(
          state.active_encounter,
          "legacy_encounter"
        ),
        status: legacyEnemyIds.length > 0 ? "active" : "resolved",
        locationLabel: getCurrentLocationLabel(state),
        enemyIds: legacyEnemyIds,
        summary:
          legacyEnemyIds.length > 0
            ? `当前仍有 ${legacyEnemyIds.length} 名敌对单位。`
            : "该遭遇已经结束。",
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
  if (quest.mainline) {
    return `主线 ${Math.max(0, quest.progress)}%`;
  }
  return `进度 ${Math.max(0, quest.progress)}`;
}

export function getEncounterSummary(entry: EncounterDisplayEntry): string {
  const enemySummary =
    entry.enemyIds.length > 0
      ? `敌对单位 ${entry.enemyIds.length}`
      : "暂无敌对单位";
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
    mainQuestPriority(left) - mainQuestPriority(right) ||
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

function normalizeQuestSummary(
  rawSummary: string | null | undefined
): string | null {
  const summary = rawSummary?.trim();
  if (!summary) {
    return null;
  }
  if (summary.startsWith("Opening objective tied to ")) {
    const location = summary
      .replace("Opening objective tied to ", "")
      .replace(/\.$/, "");
    return `开局目标指向 ${location}`;
  }
  if (summary === "A new objective has entered the scene.") {
    return "新的目标已经进入当前章节。";
  }
  return summary;
}

function injectMainQuestEntry(
  state: GameState,
  questEntries: QuestDisplayEntry[]
): QuestDisplayEntry[] {
  const mainQuest = state.world_config.world_book.campaign_context?.main_quest;
  const mainQuestTitle = mainQuest?.title?.trim();
  if (!mainQuest || !mainQuestTitle) {
    return questEntries;
  }

  const normalizedMainQuestTitle = normalizeQuestLookup(mainQuestTitle);
  const hasDuplicate = questEntries.some(
    (quest) =>
      quest.id === mainQuest.quest_id ||
      normalizeQuestLookup(quest.title) === normalizedMainQuestTitle
  );
  if (hasDuplicate) {
    return questEntries;
  }

  const summary = [mainQuest.final_goal?.trim(), mainQuest.summary?.trim()]
    .filter(Boolean)
    .join(" · ");
  const progress = clamp(Math.round(mainQuest.progress_percent ?? 0), 0, 100);

  return [
    {
      id: mainQuest.quest_id || "quest_main",
      title: mainQuestTitle,
      status: progress >= 100 ? "completed" : "active",
      summary: summary || null,
      progress,
      legacy: true,
      mainline: true
    },
    ...questEntries
  ];
}

function normalizeEncounterLabel(
  rawLabel: string | null | undefined,
  fallbackId: string
): string {
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

function mainQuestPriority(quest: QuestDisplayEntry): number {
  return quest.mainline ? -1 : 0;
}

function normalizeQuestLookup(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "");
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
    return "开局遭遇";
  }
  return "当前遭遇";
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
