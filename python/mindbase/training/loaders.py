from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..actions import kind_for_style
from ..lexicon import text_affect
from ..noise import clip
from .dataset import StorySample

BASE_STATE = {
    "stress": 0.20,
    "mood": 0.0,
    "v": 0.0,
    "a": 0.50,
    "social": 0.60,
    "energy": 0.70,
    "safety": 0.70,
    "curiosity": 0.50,
    "identity": 0.70,
    "willpower": 0.80,
    "confidence": 0.60,
}

EMOTION_MAP = {
    "negative-other": (-0.5, 0.5),
    "positive-other": (0.6, 0.5),
    "depress": (-0.6, 0.2),
    "worried": (-0.5, 0.7),
    "relaxed": (0.5, 0.2),
    "grateful": (0.7, 0.4),
    "astonished": (0.1, 0.8),
    "disgust": (-0.6, 0.5),
    "sad": (-0.6, 0.3),
    "sadness": (-0.6, 0.3),
    "悲伤": (-0.6, 0.3),
    "难过": (-0.6, 0.3),
    "angry": (-0.5, 0.8),
    "anger": (-0.5, 0.8),
    "愤怒": (-0.5, 0.8),
    "anxious": (-0.5, 0.7),
    "anxiety": (-0.5, 0.7),
    "焦虑": (-0.5, 0.7),
    "fear": (-0.6, 0.8),
    "害怕": (-0.6, 0.8),
    "joy": (0.7, 0.6),
    "happy": (0.7, 0.6),
    "开心": (0.7, 0.6),
    "neutral": (0.0, 0.5),
    "中性": (0.0, 0.5),
}

STRATEGY_ACTION = {
    "question": "speak",
    "restatement or paraphrasing": "repair",
    "reflection of feelings": "repair",
    "affirmation and reassurance": "repair",
    "self-disclosure": "approach",
    "suggestions": "approach",
    "information": "approach",
    "others": "avoid",
    "statement-non-opinion": "speak",
    "statement-opinion": "speak",
    "answer": "speak",
    "acknowledge": "speak",
    "interjection": "speak",
    "quotation": "speak",
    "greeting": "speak",
    "appreciation": "speak",
    "thanking": "speak",
    "command": "approach",
    "irony": "avoid",
    "disagreement": "avoid",
    "reject": "avoid",
    "comfort": "repair",
    "apology": "repair",
}


def _read_any(path: Path) -> Any:
    path = Path(path)
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path.read_text(encoding="utf-8"))


def _dialog(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("dialog", "dialogue", "conversations", "utterances"):
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("utterances"), list):
            return value["utterances"]
    return []


def _text(utt: Dict[str, Any]) -> str:
    return str(utt.get("content") or utt.get("text") or utt.get("utterance") or "").strip()


def _role(utt: Dict[str, Any], index: int = 0) -> str:
    role = str(utt.get("speaker") or utt.get("role") or "").lower()
    if not role:
        return "user" if index % 2 == 0 else "supporter"
    return role


def _is_user(role: str) -> bool:
    return any(k in role for k in ("user", "customer", "seeker", "client", "0"))


def _appraisal(text: str, emotion: str = "", da: str = "") -> Dict[str, float]:
    v, a = text_affect(text)
    key = str(emotion or "").strip().lower()
    if key in EMOTION_MAP:
        v, a = EMOTION_MAP[key]
    da_key = str(da or "").lower()
    social = 0.45
    if any(k in da_key for k in ("question", "comfort", "apology", "affirmation", "reflection", "thanking")):
        social += 0.20
    if any(w in text for w in ("你", "我们", "一起", "老板", "朋友", "陪")):
        social += 0.10
    relevance = clip(0.40 + 0.40 * abs(v) + (0.20 if "question" in da_key else 0.0), 0.0, 1.0)
    return {
        "valence": round(clip(v, -1.0, 1.0), 4),
        "arousal": round(clip(a, 0.0, 1.0), 4),
        "threat": round(clip(max(0.0, -v) * 0.7, 0.0, 1.0), 4),
        "social": round(clip(social, 0.0, 1.0), 4),
        "relevance": round(relevance, 4),
    }


def _next_state(state: Dict[str, float], ap: Dict[str, float]) -> Dict[str, float]:
    v = clip(0.6 * state["v"] + 0.4 * ap["valence"], -1.0, 1.0)
    stress = clip(0.8 * state["stress"] + 0.2 * ap["threat"], 0.0, 1.0)
    social = clip(0.8 * state["social"] + 0.2 * (0.5 + 0.5 * ap["social"]), 0.0, 1.0)
    mood = clip(0.8 * state["mood"] + 0.2 * v, -1.0, 1.0)
    return {
        "stress": round(stress, 4),
        "social": round(social, 4),
        "mood": round(mood, 4),
        "energy": round(clip(state["energy"] - 0.01, 0.0, 1.0), 4),
        "identity": round(clip(state["identity"] + 0.001, 0.0, 1.0), 4),
    }


def _action(reply: str, strategy: str = "") -> str:
    key = str(strategy or "").strip().lower()
    if key in STRATEGY_ACTION:
        return STRATEGY_ACTION[key]
    text = reply or ""
    if "？" in text or "?" in text:
        return "speak"
    if any(w in text for w in ("理解", "难受", "听", "陪", "抱歉", "不容易")):
        return "repair"
    if any(w in text for w in ("建议", "试试", "可以", "一起")):
        return "approach"
    if any(w in text for w in ("不知道", "算了", "还好", "不想")):
        return "avoid"
    return "speak"


def _make_sample(
    event: str,
    reply: str,
    state: Dict[str, float],
    ap: Dict[str, float],
    action: str,
    group: str = "",
    reaction: str = "",
    reaction_emotion: str = "",
    reaction_da: str = "",
    domain: str = "chat",
    action_kind: str = "",
    modality: str = "text",
) -> StorySample:
    if reaction:
        reaction_ap = _appraisal(reaction, reaction_emotion, reaction_da)
        outcome = _next_state(state, reaction_ap)
        reward = clip(
            0.60 * (reaction_ap["valence"] - ap["valence"])
            + 0.20 * (reaction_ap["social"] - ap["social"])
            - 0.20 * reaction_ap["threat"],
            -1.0,
            1.0,
        )
    else:
        reply_ap = _appraisal(reply)
        outcome = _next_state(state, reply_ap)
        reward = clip(
            0.6 * (outcome["social"] - state["social"])
            + 0.4 * (outcome["mood"] - state["mood"])
            - 0.3 * outcome["stress"],
            -1.0,
            1.0,
        )
    return StorySample(
        event=event,
        state=dict(state),
        appraisal=ap,
        action=action,
        outcome=outcome,
        reward=round(reward, 4),
        group=group,
        domain=domain,
        action_kind=action_kind or kind_for_style(action),
        modality=modality,
    )


def load_cped(path: Path | str, limit: Optional[int] = None) -> List[StorySample]:
    path = Path(path)
    out: List[StorySample] = []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        groups: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            gid = str(row.get("Dialogue_ID") or row.get("dialogue_id") or "0")
            groups.setdefault(gid, []).append(row)
        for gid, group in groups.items():
            group.sort(key=lambda r: str(r.get("Utterance_ID") or ""))
            state = dict(BASE_STATE)
            for i in range(len(group) - 1):
                event = str(group[i].get("Utterance") or "").strip()
                reply = str(group[i + 1].get("Utterance") or "").strip()
                if not event or not reply:
                    continue
                emotion = str(group[i].get("Emotion") or "")
                event_da = str(group[i].get("DA") or "")
                strategy = str(group[i + 1].get("DA") or "")
                reaction = str(group[i + 2].get("Utterance") or "").strip() if i + 2 < len(group) else ""
                reaction_emotion = str(group[i + 2].get("Emotion") or "") if i + 2 < len(group) else ""
                reaction_da = str(group[i + 2].get("DA") or "") if i + 2 < len(group) else ""
                ap = _appraisal(event, emotion, event_da)
                sample = _make_sample(
                    event, reply, state, ap, _action(reply, strategy),
                    group=f"cped:{gid}", reaction=reaction,
                    reaction_emotion=reaction_emotion, reaction_da=reaction_da,
                )
                out.append(sample)
                state.update(_next_state(state, _appraisal(reaction or reply, reaction_emotion, reaction_da)))
                if limit and len(out) >= limit:
                    return out
        return out

    data = _read_any(path)
    dialogues = data if isinstance(data, list) else data.get("data", [])
    for index, obj in enumerate(dialogues):
        state = dict(BASE_STATE)
        dialog = _dialog(obj)
        emotion = str(obj.get("emotion") or obj.get("label") or "")
        for i, utt in enumerate(dialog):
            role = _role(utt, i)
            if not _is_user(role):
                continue
            event = _text(utt)
            reply = ""
            strategy = ""
            for nxt in dialog[i + 1:]:
                if not _is_user(_role(nxt, i + 1)):
                    reply = _text(nxt)
                    strategy = str(nxt.get("strategy") or "")
                    break
            if not event or not reply:
                continue
            reaction = ""
            reaction_emotion = ""
            reaction_da = ""
            for nxt in dialog[i + 2:]:
                if _is_user(_role(nxt, i + 2)):
                    reaction = _text(nxt)
                    reaction_emotion = str(nxt.get("emotion") or "")
                    reaction_da = str(nxt.get("strategy") or "")
                    break
            ap = _appraisal(event, emotion, str(utt.get("strategy") or ""))
            sample = _make_sample(
                event, reply, state, ap, _action(reply, strategy),
                group=f"cped:{index}", reaction=reaction,
                reaction_emotion=reaction_emotion, reaction_da=reaction_da,
            )
            out.append(sample)
            state.update(_next_state(state, _appraisal(reaction or reply, reaction_emotion, reaction_da)))
            if limit and len(out) >= limit:
                return out
    return out


def load_esconv(path: Path | str, limit: Optional[int] = None) -> List[StorySample]:
    data = _read_any(Path(path))
    dialogues = data if isinstance(data, list) else data.get("data", [])
    out: List[StorySample] = []
    for index, obj in enumerate(dialogues):
        state = dict(BASE_STATE)
        dialog = _dialog(obj)
        emotion = str(obj.get("emotion_type") or obj.get("emotion") or "")
        for i, utt in enumerate(dialog):
            role = _role(utt, i)
            if not _is_user(role):
                continue
            event = _text(utt)
            reply = ""
            strategy = ""
            for nxt in dialog[i + 1:]:
                if not _is_user(_role(nxt, i + 1)):
                    reply = _text(nxt)
                    strategy = str(nxt.get("strategy") or "")
                    break
            if not event or not reply:
                continue
            reaction = ""
            reaction_emotion = ""
            reaction_da = ""
            for nxt in dialog[i + 2:]:
                if _is_user(_role(nxt, i + 2)):
                    reaction = _text(nxt)
                    reaction_emotion = str(nxt.get("emotion") or "")
                    reaction_da = str(nxt.get("strategy") or "")
                    break
            ap = _appraisal(event, emotion)
            sample = _make_sample(
                event, reply, state, ap, _action(reply, strategy),
                group=f"esconv:{index}", reaction=reaction,
                reaction_emotion=reaction_emotion, reaction_da=reaction_da,
            )
            out.append(sample)
            state.update(_next_state(state, _appraisal(reaction or reply, reaction_emotion, reaction_da)))
            if limit and len(out) >= limit:
                return out
    return out


def load_emobank(path: Path | str, limit: Optional[int] = None) -> List[StorySample]:
    out: List[StorySample] = []
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = _read_any(path)
        rows: Iterable[Dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    for index, row in enumerate(rows):
        text = str(row.get("text") or row.get("sentence") or "").strip()
        if not text:
            continue
        d = 0.0
        if row.get("V") is not None:
            v = (float(row["V"]) - 3.0) / 2.0
            a = (float(row["A"]) - 1.0) / 4.0
            if row.get("D") not in (None, ""):
                d = (float(row["D"]) - 3.0) / 2.0
        else:
            v = float(row.get("valence") or 0.0)
            a = float(row.get("arousal") or 0.5)
            if row.get("dominance") not in (None, ""):
                d = float(row.get("dominance") or 0.0)
        state = dict(BASE_STATE)
        ap = {
            "valence": round(clip(v, -1.0, 1.0), 4),
            "arousal": round(clip(a, 0.0, 1.0), 4),
            "dominance": round(clip(d, -1.0, 1.0), 4),
            "threat": round(clip(max(0.0, -v) * 0.7, 0.0, 1.0), 4),
            "social": 0.5,
            "relevance": 0.7,
        }
        outcome = _next_state(state, ap)
        out.append(
            StorySample(
                event=text,
                state=state,
                appraisal=ap,
                action="appraise",
                outcome=outcome,
                reward=round(clip(v, -1.0, 1.0), 4),
                group=str(row.get("split") or row.get("id") or f"emobank:{index}"),
                domain="appraisal",
                action_kind="appraise",
                modality="none",
            )
        )
        if limit and len(out) >= limit:
            return out
    return out


def load_all(paths: Optional[Dict[str, str]] = None) -> List[StorySample]:
    base = Path(__file__).resolve().parent.parent / "data" / "samples"
    paths = paths or {
        "cped": str(base / "cped_sample.json"),
        "esconv": str(base / "esconv_sample.json"),
        "emobank": str(base / "emobank_sample.csv"),
    }
    samples: List[StorySample] = []
    if paths.get("cped"):
        samples.extend(load_cped(paths["cped"]))
    if paths.get("esconv"):
        samples.extend(load_esconv(paths["esconv"]))
    if paths.get("emobank"):
        samples.extend(load_emobank(paths["emobank"]))
    return samples


def load_when2speak(path: Path | str, limit: Optional[int] = None) -> List[StorySample]:
    """When2Speak token dataset: assistant '<' = SPEAK, '>' = SILENT.

    The agent's timing decision is the label; the event is the recent user context.
    """
    path = Path(path)
    out: List[StorySample] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for index, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            messages = obj.get("messages") or []
            users: List[str] = []
            label = ""
            for msg in messages:
                role = str(msg.get("role") or "")
                content = str(msg.get("content") or "").strip()
                if role == "user" and content:
                    if content.startswith("Speaker_") and ":" in content:
                        content = content.split(":", 1)[1].strip()
                    users.append(content)
                elif role == "assistant":
                    label = content
            if not users or not label:
                continue
            if "<" in label:
                action = "speak"
            elif ">" in label:
                action = "silence"
            else:
                continue
            context = (chr(10) * 1).join(users[-4:])
            event = context[-600:]
            state = dict(BASE_STATE)
            ap = _appraisal(event)
            out.append(
                StorySample(
                    event=event,
                    state=state,
                    appraisal=ap,
                    action=action,
                    outcome={},
                    reward=0.0,
                    group=f"when2speak:{index}",
                    domain="timing",
                    action_kind=kind_for_style(action),
                    modality="none",
                )
            )
            if limit and len(out) >= limit:
                break
    return out
