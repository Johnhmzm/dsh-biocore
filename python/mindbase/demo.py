from __future__ import annotations

import argparse
from typing import List

from .config import MindConfig
from .engine import MindBase
from .llm_adapter import LLMAdapter, ScriptedLLM
from .llm_openai import LLMClientError
from .llm_setup import build_client, ensure_config, probe
from .interaction_log import InteractionLogger
from .types import Event


def _print_turn(name: str, result) -> None:
    out = result.outcome
    print(f"\n{name}")
    print("  她：沉默" if result.reply is None else f"  她：{result.reply}")
    print(f"  动作：{out.action} / {out.reason}  act={out.act.kind if out.act else None}")
    print(f"  情绪：{out.prompt_state.get('occ')}  stress={out.prompt_state.get('stress')}  mood={out.prompt_state.get('mood')}")
    print(f"  关系：{out.prompt_state.get('relationship')}  identity={out.prompt_state.get('identity')}")
    tom = out.prompt_state.get("tom", {})
    ws = out.prompt_state.get("workspace", {})
    print(f"  ToM agents={tom.get('agents')}  workspace={ws.get('winner')}")


def _heads(advice=None):
    return {"action_advice": advice} if advice is not None else None


def _config(seed: int, advice=None):
    return MindConfig(seed=seed)


def run_script(seed: int = 42, client=None, advice=None, domain: str = "chat") -> List[object]:
    mb = MindBase(_config(seed, advice), heads=_heads(advice))
    replies = [
        "我在。",
        "听起来你今天真的很累。",
        "被老板骂，确实很难受。",
        "……你这句话有点重。",
        "谢谢你愿意说回来。",
    ]
    adapter = LLMAdapter(mb, client or ScriptedLLM(replies=replies, default="嗯。"))
    script = [
        (Event(text="在吗", speaker="A", directed=True, ts=0, domain=domain), 0.6),
        (Event(text="今天工作好多", speaker="A", directed=True, ts=3600, domain=domain), 0.5),
        (Event(text="老板骂我了", speaker="A", directed=True, ts=7200, domain=domain), 0.5),
        (Event(text="你根本不懂我", speaker="A", directed=True, ts=10800, domain=domain), -0.9),
        (Event(text="刚才是我不好", speaker="A", directed=True, ts=14400, domain=domain), 0.9),
    ]
    results = []
    for ev, fb in script:
        result = adapter.handle(ev, feedback=fb)
        _print_turn(f"用户：{ev.text}", result)
        results.append(result)
    return results


class ChineseFallback:
    """Small local fallback for demo; not an LLM."""

    def generate(self, request) -> str:
        text = str(request.context.get("last_user") or "")
        if any(k in text for k in ("累", "压力", "难受", "烦")):
            return "听起来你真的很累。"
        if any(k in text for k in ("在吗", "你好", "hi", "hello")):
            return "我在。"
        if "？" in text or "?" in text:
            return "我在想，你愿意多说一点吗？"
        if any(k in text for k in ("不懂", "讨厌", "滚")):
            return "……我感觉你有点生气。"
        return "嗯，我在听。"


def interactive(seed: int = 42, client=None, advice=None, domain: str = "chat") -> None:
    mb = MindBase(_config(seed, advice), heads=_heads(advice))
    adapter = LLMAdapter(mb, client or ChineseFallback())
    log = InteractionLogger()
    pending = None
    print(f"直接输入内容开始；exit / quit 退出。交互日志：{log.path}")
    while True:
        try:
            text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit", "q"):
            break
        if not text:
            continue
        if pending is not None:
            pending["user_response"] = text
            pending["latency"] = round(max(0.0, (mb.t + 60) - float(pending.get("t", 0))), 3)
            log.record(pending)
            pending = None
        state_before = mb.snapshot()
        directed = domain != "embodied"
        try:
            result = adapter.handle(Event(text=text, speaker="A", directed=directed, ts=mb.t + 60, domain=domain), feedback=0.2)
        except LLMClientError as e:
            print(f"\nAPI 调用失败：{e}")
            print("如果 401：key 为空或无效。重填：")
            print("  python -m mindbase.demo --api --reset-api")
            print("切回本地 fallback。")
            adapter.client = ChineseFallback()
            result = adapter.handle(Event(text=text, speaker="A", directed=directed, ts=mb.t + 60, domain=domain), feedback=0.2)
        _print_turn("", result)
        out = result.outcome
        pending = {
            "t": mb.t,
            "state_before": state_before,
            "event": text,
            "action": out.action,
            "reason": out.reason,
            "act": out.act.kind if out.act else None,
            "llm_request": bool(out.llm_request),
            "state_after": mb.snapshot(),
        }
    if pending is not None:
        pending["user_response"] = None
        pending["latency"] = None
        log.record(pending)


def main() -> None:
    parser = argparse.ArgumentParser(description="MindBase demo")
    parser.add_argument("--script", action="store_true", help="run scripted demo instead of interactive")
    parser.add_argument("--api", action="store_true", help="use DeepSeek/OpenAI-compatible API")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--setup", action="store_true", help="fill/save API config, then start")
    parser.add_argument("--reset-api", action="store_true", help="force re-enter API key")
    parser.add_argument("--test-api", action="store_true", help="test saved API key and exit")
    parser.add_argument("--behavior", action="store_true", help="use BEHAVIOR action advice (needs local embedding server)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    advice = None
    if args.behavior:
        try:
            from .training.behavior_advice import BehaviorAdvice
            advice = BehaviorAdvice()
            print("BEHAVIOR advice loaded")
        except Exception as e:
            print(f"BEHAVIOR advice 不可用：{e}")
            print("先启动 start_embedding.bat，并确保 behavior_policy.pt 存在。")
    client = None
    if args.api or args.setup or args.test_api:
        cfg = ensure_config(args.api_key, args.base_url, args.model, force=args.reset_api)
        print(f"API: {cfg['model']} @ {cfg['base_url']}  key={cfg['api_key'][:4]}...{cfg['api_key'][-4:]}")
        if args.test_api:
            probe(cfg)
            return
        client = build_client(cfg)
    domain = "embodied" if advice is not None else "chat"
    if args.script:
        run_script(args.seed, client=client, advice=advice, domain=domain)
    else:
        interactive(args.seed, client=client, advice=advice, domain=domain)


if __name__ == "__main__":
    main()
