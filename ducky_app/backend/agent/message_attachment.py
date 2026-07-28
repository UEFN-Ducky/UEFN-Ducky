"""Shared attachment dataclass for chat multimodal messages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MessageAttachment:
    kind: str  # image | file
    name: str
    mime: str = ""
    data_base64: str = ""
    text: str = ""
