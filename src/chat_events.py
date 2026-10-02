"""Typed events emitted by the transport-independent chat application service."""

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ChatEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class StatusEvent(ChatEvent):
    type: Literal["status"] = "status"
    stage: Literal["retrieving", "reranking", "generating"]
    message: str


class DeltaEvent(ChatEvent):
    type: Literal["delta"] = "delta"
    text: str


class SourceItem(BaseModel):
    label: str


class SourcesEvent(ChatEvent):
    type: Literal["sources"] = "sources"
    items: list[SourceItem]


class MetadataEvent(ChatEvent):
    type: Literal["metadata"] = "metadata"
    elapsed_ms: int = Field(alias="elapsedMs")
    response_source: Literal["rag", "direct", "cache", "guardrail"] = Field(
        alias="responseSource"
    )
    cache_hit: bool = Field(alias="cacheHit")


class DoneEvent(ChatEvent):
    type: Literal["done"] = "done"


class ErrorEvent(ChatEvent):
    type: Literal["error"] = "error"
    code: Literal["internal_error"] = "internal_error"
    message: str = "暫時無法完成回答，請稍後再試。"


ChatStreamEvent = Union[
    StatusEvent,
    DeltaEvent,
    SourcesEvent,
    MetadataEvent,
    DoneEvent,
    ErrorEvent,
]
