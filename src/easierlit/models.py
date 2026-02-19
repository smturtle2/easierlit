from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class IncomingMessage(BaseModel):
    thread_id: str
    session_id: str
    message_id: str
    content: str
    elements: list[Any] = Field(default_factory=list)
    author: str
    created_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutgoingCommand(BaseModel):
    command: Literal[
        "add_message",
        "add_tool",
        "update_message",
        "update_tool",
        "delete",
        "close",
    ]
    thread_id: str | None = None
    message_id: str | None = None
    content: str | None = None
    elements: list[Any] = Field(default_factory=list)
    author: str = "Assistant"
    step_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
