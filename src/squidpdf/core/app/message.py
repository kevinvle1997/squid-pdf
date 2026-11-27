from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

# A number stays a number and a list a list, so each language writes it its own way.
type Param = str | int | float | list[str]


class MessageInfo(TypedDict):
    code: str
    params: dict[str, Param]


class SaidInfo(MessageInfo):
    detail: str


@dataclass(frozen=True, slots=True)
class Message:
    key: str  # renamed only with the browser, which branches on it
    params: dict[str, Param] = field(default_factory=dict)

    def as_info(self) -> MessageInfo:
        return {"code": self.key, "params": self.params}

    @staticmethod
    def from_info(info: MessageInfo) -> Message:
        return Message(info["code"], info["params"])
