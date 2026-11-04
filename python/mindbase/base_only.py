from __future__ import annotations

from typing import Iterable, List, Optional

from .config import MindConfig
from .engine import MindBase
from .types import Event, Outcome


def run(events: Optional[Iterable[Event]] = None) -> List[Outcome]:
    """Base-only mode: no LLM, no client, only the base's own decisions."""
    mb = MindBase(MindConfig(seed=700))
    evs = list(events or [
        Event(text="在吗", speaker="A", directed=True, ts=0),
        Event(text="今天有点累", speaker="A", ts=60),
        Event(text="老板，出来玩吗", speaker="A", directed=True, ts=120),
        Event(kind="tick", ts=180),
    ])
    return mb.run(evs)


def main() -> None:
    for out in run():
        print(out.t, out.action, out.reason, out.prompt_state.get("relationship"))


if __name__ == "__main__":
    main()
