export interface WorldGlossary {
  stats: Record<string, string>;
  damage_types: Record<string, string>;
  item_categories: Record<string, string>;
}

export interface FanficMetaData {
  base_ip: string;
  universe_type: string;
  tone_and_style: string;
}

export interface CampaignContext {
  era_and_timeline: string;
  macro_world_state: string;
  looming_crisis: string;
  opening_scene: string;
}

export interface WorldBook {
  campaign_context: CampaignContext;
}

export interface WorldNode {
  node_id: string;
  title: string;
  base_desc: string;
  hidden_detail_dc10?: string | null;
  deep_secret_dc18?: string | null;
  tags: string[];
}

export interface WorldTopology {
  start_node_id: string;
  nodes: Record<string, WorldNode>;
  edges: Record<string, string[]>;
}

export interface WorldConfig {
  world_id: string;
  theme: string;
  fanfic_meta: FanficMetaData;
  world_book: WorldBook;
  glossary: WorldGlossary;
  starting_location: string;
  key_npcs: string[];
  initial_quests: string[];
  mechanics: Record<string, unknown>;
  topology: WorldTopology;
}

export type QuestStatus = "active" | "completed" | "failed";
export type EncounterStatus = "active" | "resolved" | "escaped";

export interface TimingStage {
  stage_id: string;
  label: string;
  duration_ms: number;
}

export interface RequestTelemetry {
  total_ms: number;
  stages: TimingStage[];
}

export interface PlayerState {
  stats: Record<string, number>;
  attributes: Record<string, number>;
  inventory: Record<string, number>;
  temporary_items: Record<string, string>;
}

export interface RuntimeEntityState {
  stats: Record<string, number>;
  attributes: Record<string, number>;
  tags: string[];
}

export interface QuestState {
  quest_id: string;
  title: string;
  status: QuestStatus;
  summary?: string | null;
  progress: number;
}

export interface EncounterState {
  encounter_id: string;
  label: string;
  status: EncounterStatus;
  location_id: string;
  enemy_ids: string[];
  summary?: string | null;
}

export interface GameState {
  session_id: string;
  player: PlayerState;
  current_location_id: string;
  active_encounter?: string | null;
  encounter_entities: Record<string, RuntimeEntityState>;
  quest_log?: Record<string, QuestState>;
  encounter_log?: Record<string, EncounterState>;
  world_config: WorldConfig;
}

export interface ContextEntitySnapshot {
  entity_id: string;
  display_name: string;
  entity_type: string;
  summary?: string | null;
}

export interface LootTargetSnapshot {
  target_id: string;
  display_name: string;
  entity_type: string;
  summary: string;
  source_enemy_id?: string | null;
}

export interface MutationLog {
  action: "add" | "subtract" | "set" | "delete" | "append";
  target_path: string;
  value: unknown;
  reason: string;
}

export interface ExecutedEvent {
  event_type: string;
  is_success: boolean;
  actor: string;
  target: string;
  abstract_action: string;
  result_tags: string[];
}

export interface StoryLog {
  id: string;
  role: "user" | "system";
  text: string;
  animate?: boolean;
  timestamp: number;
}

export interface AuditPacket {
  id: string;
  created_at: number;
  executed_events: ExecutedEvent[];
  mutation_logs: MutationLog[];
  topology_snapshot?: WorldTopology | null;
}

export interface RuntimeSessionSnapshot {
  recent_visible_text?: string | null;
  nearby_npcs: ContextEntitySnapshot[];
  encounter_names: Record<string, string>;
  lootable_targets: Record<string, LootTargetSnapshot>;
  temp_item_counter: number;
  dynamic_location_counter: number;
}

export interface SaveSnapshot {
  version: number;
  world_prompt: string;
  game_state: GameState;
  story_logs: StoryLog[];
  audit_trail: AuditPacket[];
  audit_panel_open: boolean;
  runtime_snapshot: RuntimeSessionSnapshot;
}

export interface SaveSlot {
  id: string;
  label: string;
  saved_at: number;
  snapshot: SaveSnapshot;
}

export interface WorldGenerateResponse {
  world_config: WorldConfig;
  prologue_text?: string | null;
  telemetry?: RequestTelemetry | null;
}

export interface GameStartRequest {
  world_config: WorldConfig;
  world_prompt?: string;
  prologue_text?: string | null;
}

export interface GameSaveRequest {
  session_id: string;
}

export interface GameSaveResponse {
  runtime_snapshot: RuntimeSessionSnapshot;
}

export interface GameRestoreRequest {
  world_prompt?: string;
  game_state: GameState;
  runtime_snapshot: RuntimeSessionSnapshot;
}

export interface GameRestoreResponse {
  session_id: string;
  current_state: GameState;
}

export interface GameResetRequest {
  session_id: string;
}

export interface GameResetResponse {
  ok: boolean;
}

export interface GameTurnResponse {
  session_id: string;
  current_state: GameState;
  narration: string;
  executed_events?: ExecutedEvent[];
  mutation_logs?: MutationLog[];
  telemetry?: RequestTelemetry | null;
}

export interface GameActionRequest {
  session_id: string;
  user_input: string;
}
