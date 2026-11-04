from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Dict, Optional

import urllib.error
import urllib.request

from .llm_openai import OpenAICompatClient

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def config_path() -> Path:
    return Path.home() / ".mindbase" / "llm.json"


def load_config(path: Path | str | None = None) -> Dict[str, str]:
    p = Path(path) if path else config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: Dict[str, str], path: Path | str | None = None) -> Path:
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def resolve_config(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    path: Path | str | None = None,
) -> Dict[str, str]:
    saved = load_config(path)
    key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or saved.get("api_key", "")
    base = base_url or os.getenv("LLM_BASE_URL") or saved.get("base_url") or DEFAULT_BASE
    name = model or os.getenv("LLM_MODEL") or saved.get("model") or DEFAULT_MODEL
    return {"api_key": key, "base_url": base, "model": name}


def ensure_config(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    path: Path | str | None = None,
    force: bool = False,
) -> Dict[str, str]:
    cfg = resolve_config(api_key, base_url, model, path)
    if force or not cfg["api_key"]:
        print("\n首次使用需要填 DeepSeek API Key。")
        print("获取：https://platform.deepseek.com/api_keys")
        cfg["api_key"] = getpass.getpass("API Key（输入不回显）：").strip()
        cfg["base_url"] = input(f"Base URL [{cfg['base_url']}]: ").strip() or cfg["base_url"]
        cfg["model"] = input(f"Model [{cfg['model']}]: ").strip() or cfg["model"]
        if not cfg["api_key"]:
            raise RuntimeError("没有 API Key，无法调用")
        save_config(cfg, path)
        print(f"已保存到 {Path(path) if path else config_path()}（明文，注意不要泄露）")
    return cfg


def build_client(cfg: Dict[str, str]) -> OpenAICompatClient:
    return OpenAICompatClient(model=cfg["model"], base_url=cfg["base_url"], api_key=cfg["api_key"])


def probe(cfg: Dict[str, str]) -> None:
    key = cfg["api_key"]
    print(f"key: len={len(key)} prefix={key[:5]} suffix={key[-4:]}")
    bases = [cfg["base_url"]]
    raw = cfg["base_url"].rstrip("/")
    if raw.endswith("/v1"):
        bases.append(raw[:-3])
    else:
        bases.append(raw + "/v1")
    for base in dict.fromkeys(bases):
        url = base.rstrip("/") + "/chat/completions"
        body = json.dumps({"model": cfg["model"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}).encode()
        req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        print("\nENDPOINT", url)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print("STATUS", r.status)
                print(r.read().decode("utf-8", errors="replace")[:600])
        except urllib.error.HTTPError as e:
            print("STATUS", e.code)
            print(e.read().decode("utf-8", errors="replace")[:600])
        except Exception as e:
            print("ERROR", type(e).__name__, str(e)[:300])
