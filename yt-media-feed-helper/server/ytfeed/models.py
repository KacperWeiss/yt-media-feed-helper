"""Normalized models (ADR-08) shared by all sources + proposal input models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """YouTube channel, Steam game (future: RSS feed, Bluesky account)."""

    ref: str
    source: str
    kind: str
    title: str
    url: str
    meta: dict = Field(default_factory=dict)


class Item(BaseModel):
    """Video, news item (future: RSS entry, post)."""

    ref: str
    source: str
    kind: str
    entity_ref: str
    url: str
    published_at: datetime
    is_official_hint: bool | None = None
    metrics: dict[str, int | float] = Field(default_factory=dict)
    untrusted: dict[str, str] = Field(default_factory=dict)


class Reaction(BaseModel):
    """Comment, review (future: reply)."""

    ref: str
    source: str
    item_ref: str | None
    entity_ref: str
    url: str
    published_at: datetime
    stance_hint: Literal["positive", "negative"] | None = None
    weight: float = 0.0
    lang: str | None = None
    untrusted: dict[str, str] = Field(default_factory=dict)


class ProposedValue(BaseModel):
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=300)
    evidence_refs: list[str] = Field(default_factory=list)


class NewValue(BaseModel):
    dimension: str
    value: str = Field(min_length=1, max_length=80)
    rationale: str = Field(default="", max_length=300)
    parent: str | None = Field(default=None, max_length=80)  # Q-17: position in the hierarchy (e.g. Grand strategy)


class Decision(BaseModel):
    proposal_id: str
    action: Literal["accept", "reject", "accept_with_changes"]
    overrides: dict[str, list[str]] | None = None


class DimValue(BaseModel):
    dimension: str
    value: str
    parent: str | None = None


class SteamAppids(BaseModel):
    """Q-08: specialization value → Steam appid mapping (written to taxonomy.yaml via catalog_commit)."""

    value: str
    appids: list[int] = Field(max_length=20)
