from __future__ import annotations

from typing import Dict, List

from .types import Event


def replay_episodes(session, rows: List[Dict[str, object]], limit: int = 20) -> int:
    """Replay recent user episodes through low-level state updates (no LLM)."""
    n = 0
    for row in rows[-limit:]:
        text = str(row.get("text") or "")
        if not text:
            continue
        try:
            session.mind.step(Event(text=text, speaker=session.user, directed=True, ts=session.mind.t + 60))
            session.mind.post_reply(str(row.get("reply") or "嗯"), feedback=float(row.get("feedback", 0.2)), speaker=session.user)
            session.turns.append({"user": text, "reply": row.get("reply"), "action": "replay", "reason": "replay", "trust": session.mind.social.trust_for(session.user)})
            n += 1
        except Exception:
            continue
    return n
