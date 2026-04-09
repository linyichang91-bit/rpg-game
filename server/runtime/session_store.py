"""In-memory session state for the interactive sandbox API."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4

from server.schemas.core import (
    GameState,
    PlayerState,
    RuntimeEntityState,
    WorldConfig,
    WorldNode,
)
from server.schemas.orchestrator import ContextEntity


DEFAULT_WEAPON_KEY = "item_weapon_01"
DEFAULT_HP_VALUE = 20
DEFAULT_MP_VALUE = 12
DEFAULT_ENEMY_HP = 16


@dataclass
class LootTarget:
    """Server-side lootable target metadata that should not pollute core state."""

    target_id: str
    display_name: str
    entity_type: str
    summary: str
    source_enemy_id: str | None = None


@dataclass
class SessionRecord:
    """Mutable server-side session record."""

    session_id: str
    game_state: GameState
    world_prompt: str | None
    location_summary: str
    nearby_npcs: list[ContextEntity] = field(default_factory=list)
    encounter_names: dict[str, str] = field(default_factory=dict)
    lootable_targets: dict[str, LootTarget] = field(default_factory=dict)
    temp_item_counter: int = 0
    dynamic_location_counter: int = 0

    def build_nearby_entities(self) -> list[ContextEntity]:
        """Combine static NPCs, live enemies, and lootable objects for the brain."""

        entities = list(self.nearby_npcs)
        for entity_id in self.game_state.encounter_entities:
            entities.append(
                ContextEntity(
                    entity_id=entity_id,
                    display_name=self.encounter_names.get(entity_id, entity_id),
                    entity_type="enemy",
                    summary=f"Active threat near {self.current_location_title}.",
                )
            )

        for loot_target in self.lootable_targets.values():
            entities.append(
                ContextEntity(
                    entity_id=loot_target.target_id,
                    display_name=loot_target.display_name,
                    entity_type=loot_target.entity_type,
                    summary=loot_target.summary,
                )
            )

        return entities

    @property
    def current_location_node(self) -> WorldNode | None:
        return self.game_state.world_config.topology.nodes.get(self.game_state.current_location_id)

    @property
    def current_location_title(self) -> str:
        current_node = self.current_location_node
        if current_node is not None:
            return current_node.title
        return self.game_state.current_location_id

    def sync_after_state_update(self) -> None:
        """Drop display-name mappings for entities that no longer exist."""

        live_ids = set(self.game_state.encounter_entities.keys())
        self.encounter_names = {
            entity_id: name
            for entity_id, name in self.encounter_names.items()
            if entity_id in live_ids
        }
        self.location_summary = _build_location_summary(
            self.game_state.world_config,
            self.game_state.current_location_id,
        )

    def register_defeated_enemy_loot_target(self, enemy_id: str) -> LootTarget:
        """Expose a freshly defeated enemy as a searchable corpse."""

        corpse_id = f"corpse_{enemy_id}"
        display_name = self.encounter_names.get(enemy_id, enemy_id)
        loot_target = LootTarget(
            target_id=corpse_id,
            display_name=f"{display_name}的尸体",
            entity_type="corpse",
            summary=f"The remains of {display_name} are still warm.",
            source_enemy_id=enemy_id,
        )
        self.lootable_targets[corpse_id] = loot_target
        return loot_target

    def get_loot_target(self, target_id: str) -> LootTarget | None:
        """Return a lootable target by id if it exists."""

        return self.lootable_targets.get(target_id)

    def consume_loot_target(self, target_id: str) -> LootTarget | None:
        """Remove a lootable target after it has been searched."""

        return self.lootable_targets.pop(target_id, None)

    def next_temp_item_key(self) -> str:
        """Allocate a unique temporary item key for runtime-generated loot."""

        self.temp_item_counter += 1
        return f"item_temp_loot_{self.temp_item_counter:04d}"

    def next_dynamic_location_id(self) -> str:
        """Allocate a unique abstract key for a newly generated location node."""

        self.dynamic_location_counter += 1
        return f"location_dyn_{self.dynamic_location_counter:04d}"


class SessionStore:
    """Thread-safe in-memory session store."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, SessionRecord] = {}

    def create_session(
        self,
        world_config: WorldConfig,
        *,
        world_prompt: str | None = None,
    ) -> SessionRecord:
        """Create and register a new session record."""

        session_id = f"session_{uuid4().hex[:12]}"
        prepared_world_config = _ensure_world_topology(world_config)
        primary_enemy_name = _infer_enemy_name(prepared_world_config)
        encounter_entities = {
            "enemy_01": RuntimeEntityState(
                stats={"stat_hp": DEFAULT_ENEMY_HP},
                attributes={"attr_dex": 10},
                tags=["enemy"],
            )
        }

        player_stats = {"stat_hp": DEFAULT_HP_VALUE}
        if "stat_mp" in prepared_world_config.glossary.stats:
            player_stats["stat_mp"] = DEFAULT_MP_VALUE

        game_state = GameState(
            session_id=session_id,
            player=PlayerState(
                stats=player_stats,
                attributes={"attr_dex": 12, "attr_will": 12, "attr_power": 10},
                inventory={DEFAULT_WEAPON_KEY: 1},
                temporary_items={},
            ),
            current_location_id=prepared_world_config.topology.start_node_id,
            active_encounter="encounter_opening",
            encounter_entities=encounter_entities,
            world_config=prepared_world_config,
        )

        record = SessionRecord(
            session_id=session_id,
            game_state=game_state,
            world_prompt=world_prompt,
            location_summary=_build_location_summary(
                prepared_world_config,
                prepared_world_config.topology.start_node_id,
            ),
            nearby_npcs=_build_npc_entities(prepared_world_config, primary_enemy_name),
            encounter_names={"enemy_01": primary_enemy_name},
        )

        with self._lock:
            self._sessions[session_id] = record

        return record

    def get(self, session_id: str) -> SessionRecord | None:
        """Return a session record by id."""

        with self._lock:
            return self._sessions.get(session_id)

    def save(self, record: SessionRecord) -> None:
        """Persist an updated session record."""

        with self._lock:
            self._sessions[record.session_id] = record

    def delete(self, session_id: str) -> None:
        """Remove a session if it exists."""

        with self._lock:
            self._sessions.pop(session_id, None)


def _build_npc_entities(
    world_config: WorldConfig,
    primary_enemy_name: str,
) -> list[ContextEntity]:
    entities: list[ContextEntity] = []
    npc_index = 1

    for name in world_config.key_npcs:
        if name == primary_enemy_name:
            continue

        entities.append(
            ContextEntity(
                entity_id=f"npc_{npc_index:02d}",
                display_name=name,
                entity_type="npc",
                summary=f"Key character tied to {world_config.starting_location}.",
            )
        )
        npc_index += 1
        if npc_index > 3:
            break

    return entities


def _ensure_world_topology(world_config: WorldConfig) -> WorldConfig:
    prepared = world_config.model_copy(deep=True)
    topology = prepared.topology
    start_node_id = topology.start_node_id or "location_start"
    if start_node_id not in topology.nodes:
        topology.nodes[start_node_id] = WorldNode(
            node_id=start_node_id,
            title=prepared.starting_location,
            base_desc=f"这里是{prepared.starting_location}，一切冒险都将从此开始。",
            hidden_detail_dc10=f"{prepared.starting_location}里还藏着尚未被发现的线索。",
            deep_secret_dc18=f"{prepared.starting_location}深处埋着只属于这个世界的秘密。",
            tags=["starting_area"],
        )
    topology.edges.setdefault(start_node_id, [])
    return prepared


def _infer_enemy_name(world_config: WorldConfig) -> str:
    hostile_markers = (
        "orc",
        "gang",
        "patrol",
        "death eater",
        "cultist",
        "beast",
        "hunter",
        "monster",
        "enemy",
        "半兽人",
        "帮派",
        "巡逻",
        "魔",
        "怪",
        "食死徒",
        "叛忍",
        "咒灵",
    )

    for name in reversed(world_config.key_npcs):
        lowered = name.lower()
        if any(marker in lowered for marker in hostile_markers):
            return name

    if world_config.key_npcs:
        return world_config.key_npcs[-1]

    base_ip = world_config.fanfic_meta.base_ip.lower()
    if "harry potter" in base_ip or "哈利" in base_ip:
        return "食死徒巡逻兵"
    if "lord of the rings" in base_ip or "指环王" in base_ip:
        return "半兽人打手"
    if "lord of the mysteries" in base_ip or "诡秘之主" in base_ip:
        return "隐秘教团执行者"
    if "naruto" in base_ip or "火影" in base_ip:
        return "叛忍侦察兵"

    return "敌对存在"


def _build_location_summary(
    world_config: WorldConfig,
    current_location_id: str,
) -> str:
    current_node = world_config.topology.nodes.get(current_location_id)
    location_title = current_node.title if current_node is not None else world_config.starting_location
    base_desc = current_node.base_desc if current_node is not None else world_config.starting_location
    opening_quests = ", ".join(world_config.initial_quests) or "none"
    return (
        f"Current location: {location_title}. "
        f"Scene: {base_desc}. "
        f"Theme: {world_config.fanfic_meta.tone_and_style}. "
        f"Opening quests: {opening_quests}."
    )
