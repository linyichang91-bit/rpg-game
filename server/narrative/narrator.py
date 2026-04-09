"""Narrative rendering engine driven by fact-only event logs."""

from __future__ import annotations

from textwrap import dedent
from typing import Protocol

from server.llm.config import LLMSettings
from server.llm.openai_compatible import LLMGatewayError, OpenAICompatibleTextClient
from server.schemas.core import EngineBaseModel, ExecutedEvent, GameState, WorldGlossary


class NarrationPromptBundle(EngineBaseModel):
    """Prompt payload used for the narrator LLM call."""

    system_prompt: str
    user_prompt: str


class AsyncNarrationClient(Protocol):
    """Async text-generation boundary used by the narrator engine."""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Generate narration text from prompts."""


class NarratorEngine:
    """Fact-locked narrative renderer."""

    def __init__(self, llm_client: AsyncNarrationClient) -> None:
        self._llm_client = llm_client

    async def generate_narration(
        self,
        current_state: GameState,
        events: list[ExecutedEvent],
        user_input: str,
    ) -> str:
        """Render narration text for a resolved turn."""

        prompt_bundle = build_narration_prompt(
            current_state=current_state,
            events=events,
            user_input=user_input,
        )
        try:
            narration = await self._llm_client.generate_text(
                system_prompt=prompt_bundle.system_prompt,
                user_prompt=prompt_bundle.user_prompt,
            )
            if _contains_cjk(narration):
                return narration.strip()
        except LLMGatewayError:
            pass

        return render_fallback_narration(current_state, events)


async def generate_narration(
    current_state: GameState,
    events: list[ExecutedEvent],
    user_input: str,
) -> str:
    """Default env-backed narrator entrypoint."""

    narrator = build_narrator_from_env()
    return await narrator.generate_narration(current_state, events, user_input)


def build_narrator_from_env(*, env_file: str = ".env") -> NarratorEngine:
    """Create a narrator engine from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleTextClient.from_settings(settings)
    return NarratorEngine(llm_client)


def build_narration_prompt(
    *,
    current_state: GameState,
    events: list[ExecutedEvent],
    user_input: str,
) -> NarrationPromptBundle:
    """Assemble narrator prompts with glossary locks and fact-only constraints."""

    glossary_markdown = format_glossary_markdown(current_state.world_config.glossary)
    temporary_item_markdown = format_temporary_item_markdown(current_state)
    fact_summary = build_fact_summary(events)
    player_state_summary = build_player_state_summary(current_state)
    location_snapshot = build_location_snapshot(current_state)

    system_prompt = dedent(
        f"""
        你是一个文字 RPG 的无情旁白。
        当前世界主题：{current_state.world_config.theme}

        核心规则：
        1. 你必须严格服从【事实日志】。如果事实写明失败，就只能描写失败。
        2. 你绝不可发明额外事实、战果、掉落、人物反应、状态变化或后续剧情。
        3. 你只有表现权，没有结算权，绝不能输出 JSON、指令或任何会修改状态的数据。
        4. 最终文本里不要暴露 stat_hp、dmg_kinetic、item_temp_loot_0001、enemy_01 这类系统标签。
        5. 你必须使用【术语约束】和【临时物品映射】中的中文名称来替换系统标签。
        6. 如果【事实日志】没有写明获得物品，就绝对不能暗示捡到了物品。
        7. 如果【事实日志】包含复合战斗回合，请把玩家动作与敌人反击融合成一段流畅、连贯的动作描写。
        8. 当事件为 loot 且成功时，要描写玩家如何从目标上发现或剥取那些已经被事实确认的物品。
        9. 当事件为 loot 且失败时，要描写玩家仔细搜寻但一无所获，或者只摸了一手灰。
        10. 当事件带有 new_location_discovered 标签时，要表现出初次踏入未知之地的新鲜感，并优先引用当前地点资料中的 base_desc。
        11. 当事件为 exploration 且失败时，只能描写道路不通或暂时无法抵达，不能凭空开辟新路线。
        12. 最终文本必须完全使用简体中文，文风贴近中文网文读者熟悉的阅读节奏。

        术语约束：
        {glossary_markdown}

        临时物品映射：
        {temporary_item_markdown}
        """
    ).strip()

    user_prompt = dedent(
        f"""
        玩家原话：
        {user_input}

        当前地点：
        {current_state.current_location_id}

        事实日志：
        {fact_summary}

        当前地点资料：
        {location_snapshot}

        玩家当前状态摘要：
        {player_state_summary}

        请只描写这一回合已经被结算出来的结果，输出一小段简体中文叙事。
        """
    ).strip()

    return NarrationPromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def format_glossary_markdown(glossary: WorldGlossary) -> str:
    """Format glossary mappings into strict narrator-facing terminology rules."""

    sections = [
        _format_mapping_section("状态术语", glossary.stats),
        _format_mapping_section("伤害术语", glossary.damage_types),
        _format_mapping_section("物品类别", glossary.item_categories),
    ]
    return "\n\n".join(section for section in sections if section) or "- 当前没有额外术语约束。"


def format_temporary_item_markdown(current_state: GameState) -> str:
    """Expose runtime-generated item names so the narrator can use them faithfully."""

    temporary_items = current_state.player.temporary_items
    if not temporary_items:
        return "- 当前没有新注册的临时物品。"

    lines = []
    for item_key, item_name in sorted(temporary_items.items()):
        lines.append(f"- 当事实日志提到 `{item_key}` 时，请写成“{item_name}”。")
    return "\n".join(lines)


def build_fact_summary(events: list[ExecutedEvent]) -> str:
    """Serialize executed events into a compact fact log."""

    if not events:
        return "- 本回合没有已执行事件。"

    lines: list[str] = []
    for index, event in enumerate(events, start=1):
        tags = ", ".join(event.result_tags) if event.result_tags else "无"
        lines.append(
            (
                f"- 事件{index}: event_type={event.event_type}; actor={event.actor}; "
                f"target={event.target}; action={event.abstract_action}; "
                f"is_success={event.is_success}; result_tags=[{tags}]"
            )
        )
    return "\n".join(lines)


def build_player_state_summary(current_state: GameState) -> str:
    """Create a compact descriptive snapshot of the player's core resources."""

    glossary = current_state.world_config.glossary.stats
    if not current_state.player.stats:
        return "- 当前没有可追踪的玩家状态。"

    lines = []
    for key, value in sorted(current_state.player.stats.items()):
        label = glossary.get(key, key)
        lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def build_location_snapshot(current_state: GameState) -> str:
    """Expose the current location title and base description for exploration narration."""

    current_node = current_state.world_config.topology.nodes.get(current_state.current_location_id)
    if current_node is None:
        return f"- title: {current_state.current_location_id}"

    return "\n".join(
        [
            f"- title: {current_node.title}",
            f"- base_desc: {current_node.base_desc}",
        ]
    )


def _format_mapping_section(title: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return ""

    lines = [f"### {title}"]
    for abstract_key, term in sorted(mapping.items()):
        lines.append(f"- 当事实日志出现 `{abstract_key}` 时，请改写为“{term}”。")
    return "\n".join(lines)


def render_fallback_narration(
    current_state: GameState,
    events: list[ExecutedEvent],
) -> str:
    """Render a deterministic fact-locked narration when the LLM gateway fails."""

    if not events:
        return "本回合没有可供叙述的已执行事件。"

    sentences = [
        _render_fallback_event(current_state, event)
        for event in events
    ]
    status_sentence = _build_fallback_status_sentence(current_state)
    if status_sentence:
        sentences.append(status_sentence)
    return "".join(sentence for sentence in sentences if sentence)


def _render_fallback_event(
    current_state: GameState,
    event: ExecutedEvent,
) -> str:
    if event.event_type == "combat":
        return _render_fallback_combat(current_state, event)

    if event.event_type == "loot":
        return _render_fallback_loot(current_state, event)

    if event.event_type == "exploration":
        return _render_fallback_exploration(current_state, event)

    if event.event_type == "utility" and event.abstract_action == "world_entry":
        sentence = f"你踏入了{current_state.current_location_id}。"
        if current_state.world_config.initial_quests:
            sentence += f"眼下最牵动你的，是“{current_state.world_config.initial_quests[0]}”。"
        return sentence

    if event.event_type == "utility" and event.abstract_action == "state_query":
        return f"你在{current_state.current_location_id}迅速梳理了一遍自己的现状。"

    if event.is_success:
        return f"引擎记录到一次成功的“{event.abstract_action}”。"
    return f"引擎记录到一次失败的“{event.abstract_action}”。"


def _render_fallback_combat(
    current_state: GameState,
    event: ExecutedEvent,
) -> str:
    tags = set(event.result_tags)
    damage_terms = [
        current_state.world_config.glossary.damage_types[tag]
        for tag in event.result_tags
        if tag in current_state.world_config.glossary.damage_types
    ]

    if event.actor == "player" and "invalid_weapon" in tags:
        return "你伸手去抓武器，却发现那件武器根本不在你的持有物里。"
    if event.actor == "player" and "invalid_target" in tags:
        return "你这一击没有找到有效目标。"
    if event.actor == "player" and "invalid_target_state" in tags:
        return "目标当前并不处于可被这样结算的状态。"
    if event.actor != "player" and "invalid_player_state" in tags:
        return "敌人强行追击了一下，但这轮交锋在真正落下之前就已经散掉了。"

    if not event.is_success:
        if event.actor != "player":
            if "critical_miss" in tags:
                return "敌人吃痛之下仓促反扑，却把这一记反击彻底挥空了。"
            if "dodged_by_player" in tags:
                return "敌人强撑着还了一记狠招，但你及时闪开了。"
            return "敌人试图反击，但这一击并没有碰到你。"

        if "critical_miss" in tags:
            return "你这一击失手得很厉害，动作彻底落空。"
        return "你这一击没有命中。"

    if event.actor != "player":
        sentences = ["敌人硬扛住伤势，立刻回敬了一记反击。"]
    else:
        sentences = ["你的攻击命中了。"]
    if damage_terms:
        sentences.append(f"这一击带来了{', '.join(damage_terms)}。")
    if "critical_hit" in tags:
        sentences.append("这一击又快又准，几乎没有任何多余动作。")
    if "target_killed" in tags:
        sentences.append("目标当场失去了战斗能力。")
    if "player_downed" in tags:
        sentences.append("这股冲击几乎把你打得失去战斗能力。")
    return "".join(sentences)


def _render_fallback_loot(
    current_state: GameState,
    event: ExecutedEvent,
) -> str:
    if "invalid_loot_target" in event.result_tags:
        return f"你朝着{event.target}摸去，却发现眼前根本没有可供搜刮的目标。"

    found_keys = [
        tag.removeprefix("found_")
        for tag in event.result_tags
        if tag.startswith("found_item_temp_")
    ]
    found_names = [
        current_state.player.temporary_items.get(item_key, item_key)
        for item_key in found_keys
    ]

    if event.is_success and found_names:
        joined_names = "、".join(found_names)
        return f"你俯身搜查{event.target}，从中翻出了{joined_names}。"

    if "critical_search_failure" in event.result_tags:
        return f"你把{event.target}翻了个遍，最后只摸了一手灰。"

    return f"你仔细搜查了{event.target}，却一无所获。"


def _render_fallback_exploration(
    current_state: GameState,
    event: ExecutedEvent,
) -> str:
    current_node = current_state.world_config.topology.nodes.get(current_state.current_location_id)
    current_title = current_node.title if current_node is not None else event.target
    current_desc = current_node.base_desc if current_node is not None else ""

    if not event.is_success and "path_not_connected" in event.result_tags:
        return f"你试着前往{event.target}，却发现眼下并没有一条真正连通的路。"

    if "new_location_discovered" in event.result_tags:
        if current_desc:
            return f"你踏入了{current_title}。{current_desc}"
        return f"你第一次踏入了{current_title}，陌生的气息迎面压了过来。"

    return f"你转入了{current_title}。"


def _build_fallback_status_sentence(current_state: GameState) -> str:
    if not current_state.player.stats:
        return ""

    glossary = current_state.world_config.glossary.stats
    parts = [
        f"{glossary.get(key, key)} {value}"
        for key, value in sorted(current_state.player.stats.items())
    ]
    return f"当前状态：{'，'.join(parts)}。"


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
