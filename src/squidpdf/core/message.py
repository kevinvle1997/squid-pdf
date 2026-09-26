"""What the app tells a person, before it's put into anyone's words.

Below the API nothing writes a sentence: it says what happened as a Message,
the key of a sentence in `core.words` and the facts that fill it, and the edge
puts that into the reader's language. Plain data, so a worker can pickle it
back and the analysis can keep it on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

# A fact that fills a placeholder, as it is: a number stays a number and a list a
# list, so each language can write it its own way. Characters come one per item.
type Param = str | int | float | list[str]


class MessageInfo(TypedDict):
    """A Message as JSON: the sentence's key as `code`, and the facts that fill it."""

    code: str
    params: dict[str, Param]


@dataclass(frozen=True, slots=True)
class Message:
    """Which sentence to tell a person, and the facts its placeholders take.

    `key` names the sentence in `core.words` and is never renamed: the browser
    can branch on it, as it does on a Problem's type.
    """

    key: str  # snake_case, e.g. "font_not_in_file"
    params: dict[str, Param] = field(default_factory=dict)

    def as_info(self) -> MessageInfo:
        """This Message as JSON, for the browser or the disk."""
        return {"code": self.key, "params": self.params}

    @staticmethod
    def from_info(info: MessageInfo) -> Message:
        """The Message `as_info` wrote."""
        return Message(info["code"], info["params"])
