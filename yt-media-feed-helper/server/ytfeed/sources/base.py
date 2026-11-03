"""Source abstraction (ADR-08): the `Source` protocol + registry + HTTP transport.

YouTube and Steam are two `Source` implementations; in mock mode both use `MockTransport`
(fixtures), so tests go through THE SAME parsing code as production. Adding RSS/Bluesky =
a new `Source` class + `SourceRegistry.register(...)`, with no changes to the generic tools.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from ..models import Entity, Item, Reaction

Capability = Literal["entities", "items", "reactions", "search", "news", "reviews"]


class Transport(Protocol):
    """Performs a GET to the API; returns parsed JSON or raises UpstreamHTTPError / MockFixtureMissing."""

    def get(self, api: str, endpoint: str, params: dict[str, Any]) -> Any: ...


@runtime_checkable
class Source(Protocol):
    name: str
    capabilities: set[Capability]

    def resolve_entity(self, query: str) -> list[Entity]: ...

    def list_items(self, entity_refs: list[str], since: datetime, until: datetime | None, limit: int,
                   match_terms: list[str]) -> list[Item]: ...

    def list_reactions(self, target_ref: str, limit: int, order: str, match_terms: list[str], lang: str | None) -> list[Reaction]: ...

    def estimate_cost(self, op: str, **kw: Any) -> tuple[str, int]: ...


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> Source:
        return self._sources[name]

    def names(self) -> list[str]:
        return sorted(self._sources)
