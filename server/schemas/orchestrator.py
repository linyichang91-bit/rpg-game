"""Schemas for Central Brain intent parsing and routing."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictStr

from server.schemas.core import EngineBaseModel


PipelineType = Literal[
    "combat",
    "exploration",
    "loot",
    "dialogue",
    "skill_check",
    "lore_query",
    "utility",
    "ooc",
]


class OrchestratorDecision(EngineBaseModel):
    """Structured intent classification output from the Central Brain."""

    pipeline_type: PipelineType = Field(..., description="Route target pipeline")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Intent confidence score")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Pipeline-specific extracted parameters",
    )
    clarification_needed: StrictStr | None = Field(
        default=None,
        description="Clarification prompt for the player when intent is ambiguous",
    )


class PipelineSpec(EngineBaseModel):
    """Prompt-facing description of a supported runtime pipeline."""

    pipeline_type: PipelineType
    description: StrictStr
    required_parameters: list[StrictStr] = Field(default_factory=list)
    optional_parameters: list[StrictStr] = Field(default_factory=list)


class ContextEntity(EngineBaseModel):
    """Known nearby entity details used for target matching in prompts."""

    entity_id: StrictStr
    display_name: StrictStr
    entity_type: StrictStr
    summary: StrictStr | None = None


class DecisionContext(EngineBaseModel):
    """Reduced game context provided to the Central Brain."""

    session_id: StrictStr
    world_id: StrictStr
    world_theme: StrictStr
    current_location_id: StrictStr
    location_summary: StrictStr | None = None
    active_encounter: StrictStr | None = None
    active_quest_ids: list[StrictStr] = Field(default_factory=list)
    nearby_entities: list[ContextEntity] = Field(default_factory=list)


class PromptBundle(EngineBaseModel):
    """Final prompts and JSON schema payload sent to the LLM adapter."""

    system_prompt: StrictStr
    user_prompt: StrictStr
    response_schema: dict[str, Any]


class RoutingOutcome(EngineBaseModel):
    """Normalized result returned by the Central Brain to the runtime."""

    decision: OrchestratorDecision
    should_execute: bool
    clarification_message: StrictStr | None = None
    failure_reason: Literal["low_confidence", "clarification_needed"] | None = None
