export interface WorldGlossary {
  stats: Record<string, string>;
  attributes?: Record<string, string>;
  damage_types: Record<string, string>;
  item_categories: Record<string, string>;
}

export interface FanficMetaData {
  base_ip: string;
  universe_type: string;
  tone_and_style: string;
}

export interface PlayerCharacterSheet {
  name: string;
  role: string;
  summary: string;
  objective: string;
  attributes: Record<string, number>;
}

export interface CampaignContext {
  era_and_timeline: string;
  macro_world_state: string;
  looming_crisis: string;
  opening_scene: string;
  main_quest?: MainQuest | null;
  current_chapter?: CurrentChapter | null;
  milestones?: StoryMilestone[];
}

export interface MainQuest {
  quest_id: string;
  title: string;
  final_goal: string;
  summary?: string | null;
  linked_quest_id?: string | null;
  progress_percent?: number | null;
}

export interface CurrentChapter {
  chapter_id: string;
  title: string;
  objective: string;
  tension_level: number;
  progress_percent?: number | null;
  linked_quest_id?: string | null;
}

export interface StoryMilestone {
  milestone_id: string;
  title: string;
  summary?: string | null;
  is_completed: boolean;
  linked_quest_id?: string | null;
}

export interface PowerBenchmark {
  subject: string;
  offense_rating: number;
  defense_rating: number;
  notes: string;
}

export interface PowerTier {
  min_power: number;
  label: string;
}

export interface PowerScaling {
  scale_label: string;
  danger_gap_threshold: number;
  impossible_gap_threshold: number;
  benchmark_examples: PowerBenchmark[];
  power_tiers?: PowerTier[];
}

export interface WorldBook {
  campaign_context: CampaignContext;
  power_scaling?: PowerScaling | null;
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
  player_character?: PlayerCharacterSheet | null;
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
  skills?: Record<string, number>;
  skill_labels?: Record<string, string>;
  growth?: PlayerGrowthState | null;
  power_level?: number;
  rank_label?: string;
}

export interface PlayerGrowthState {
  xp: number;
  level: number;
  proficiency_bonus: number;
  unspent_stat_points: number;
  last_growth_reason?: string | null;
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

export interface PendingStoryLog
  extends Omit<StoryLog, "role" | "animate"> {
  role: "system";
  isStreaming: true;
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

export interface GameActionStreamRequest extends GameActionRequest {
  client_turn_id: string;
}

export type GameTurnStreamPhase =
  | "connecting"
  | "loading_session"
  | "resolving_tools"
  | "writing_narration"
  | "finalizing_state";

export interface TurnAcceptedEvent {
  type: "turn.accepted";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  accepted_at: number;
}

export interface TurnStatusEvent {
  type: "turn.status";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  phase: GameTurnStreamPhase;
  message: string;
  progress?: number | null;
}

export interface NarrationStartEvent {
  type: "narration.start";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  message_id: string;
  role: "system";
}

export interface NarrationDeltaEvent {
  type: "narration.delta";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  message_id: string;
  delta: string;
  chunk_index: number;
}

export interface NarrationEndEvent {
  type: "narration.end";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  message_id: string;
  full_text: string;
}

export interface TurnCompletedEvent {
  type: "turn.completed";
  session_id: string;
  client_turn_id: string;
  server_turn_id: string;
  narration: string;
  current_state: GameState;
  executed_events?: ExecutedEvent[];
  mutation_logs?: MutationLog[];
  telemetry?: RequestTelemetry | null;
}

export interface TurnErrorEvent {
  type: "turn.error";
  session_id: string;
  client_turn_id: string;
  server_turn_id?: string;
  code:
    | "session_not_found"
    | "llm_gateway_error"
    | "stream_interrupted"
    | "internal_error";
  message: string;
  retryable: boolean;
}

export interface HeartbeatEvent {
  type: "heartbeat";
  ts: number;
}

export type GameTurnStreamEvent =
  | TurnAcceptedEvent
  | TurnStatusEvent
  | NarrationStartEvent
  | NarrationDeltaEvent
  | NarrationEndEvent
  | TurnCompletedEvent
  | TurnErrorEvent
  | HeartbeatEvent;

export interface ActiveTurnStream {
  clientTurnId: string;
  serverTurnId?: string | null;
  phase: GameTurnStreamPhase;
  statusMessage: string | null;
  narration: PendingStoryLog | null;
}

export interface TurnFailureState {
  id: string;
  command: string;
  message: string;
  retryable: boolean;
  createdAt: number;
}
