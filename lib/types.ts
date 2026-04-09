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

export interface GameState {
  session_id: string;
  player: PlayerState;
  current_location_id: string;
  active_encounter?: string | null;
  encounter_entities: Record<string, RuntimeEntityState>;
  world_config: WorldConfig;
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

export interface WorldGenerateResponse {
  world_config: WorldConfig;
  telemetry?: RequestTelemetry | null;
}

export interface GameStartRequest {
  world_config: WorldConfig;
  world_prompt?: string;
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
