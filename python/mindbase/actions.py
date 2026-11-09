from __future__ import annotations

from dataclasses import dataclass

ACTION_KINDS = (
    "communicate",
    "approach",
    "avoid",
    "explore",
    "rest",
    "repair",
    "protect",
    "seek_help",
    "give_help",
    "withdraw",
    "appraise",
    "forage",
    "drink",
    "heal",
)
MODALITIES = ("text", "voice", "gesture", "none")

STYLE_TO_KIND = {
    "speak": "communicate",
    "silence": "withdraw",
    "ask": "seek_help",
    "repair": "repair",
    "avoid": "avoid",
    "approach": "approach",
    "appraise": "appraise",
    "forage": "forage",
    "rest": "rest",
    "socialize": "approach",
    "drink": "drink",
    "heal": "heal",
}


def kind_for_style(style: str) -> str:
    return STYLE_TO_KIND.get(str(style or "").lower(), "communicate")


@dataclass
class Action:
    """A domain-general action, not a chat token.

    kind: what the base does
    modality: how it is expressed (chat is only communicate/text)
    style: optional sub-style for language (speak/repair/avoid...)
    """
    kind: str = "communicate"
    modality: str = "text"
    style: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "modality": self.modality, "style": self.style}
