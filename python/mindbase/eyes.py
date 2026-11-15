"""Eyes: on-demand image understanding.

Reuses the existing vision_pipeline.py service (PixAI + MiniCPM + OCR),
launched only when an image arrives; the service exits after idle timeout.
Descriptions are cached by image sha256 with TTL + LRU, cleaned periodically.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "mindbase" / "data" / "vision_cache"
SERVICE = ROOT / "vision_pipeline.py"
PY310 = os.environ.get("VISION_PY", r"C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe")


class VisionEyes:
    def __init__(self, port: int = 8096, ttl_s: float = 7 * 86400.0,
                 max_entries: int = 200, timeout: float = 180.0,
                 cache_dir: Optional[Path] = None):
        self.port = int(os.environ.get("COMPANION_VISION_PORT", str(port)))
        self.base = "http://127.0.0.1:%d" % self.port
        self.ttl_s = float(ttl_s)
        self.max_entries = int(max_entries)
        self.timeout = float(timeout)
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_start = 0.0

    def _get(self, url: str, timeout: float = 2.0):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def _post(self, url: str, payload: dict, timeout: float):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def alive(self) -> bool:
        return self._get(self.base + "/health", 1.5) is not None

    def _ensure(self, wait: float = 90.0) -> bool:
        if self.alive():
            return True
        if not SERVICE.exists():
            return False
        now = time.time()
        if now - self._last_start < 15.0:
            # another caller is starting it
            t0 = time.time()
            while time.time() - t0 < min(wait, 20.0):
                if self.alive():
                    return True
                time.sleep(1.0)
            return False
        self._last_start = now
        env = dict(os.environ)
        env["VISION_PORT"] = str(self.port)
        env.setdefault("VISION_IDLE_EXIT_S", "120")
        try:
            subprocess.Popen([PY310, "-u", str(SERVICE)], cwd=str(ROOT), env=env,
                             creationflags=0x08000000,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
        t0 = time.time()
        while time.time() - t0 < float(wait):
            if self.alive():
                return True
            time.sleep(1.0)
        return False

    def _hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _cache_get(self, key: str) -> str:
        p = self.cache_dir / (key + ".json")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - float(d.get("t", 0)) <= self.ttl_s:
                return str(d.get("text") or "")
        except Exception:
            pass
        return ""

    def _cache_put(self, key: str, text: str) -> None:
        try:
            (self.cache_dir / (key + ".json")).write_text(
                json.dumps({"t": time.time(), "text": text}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        self.cleanup()

    def cleanup(self) -> int:
        now = time.time()
        rows = []
        try:
            for p in self.cache_dir.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    t = float(d.get("t", 0))
                except Exception:
                    t = 0.0
                if now - t > self.ttl_s:
                    p.unlink(missing_ok=True)
                else:
                    rows.append((t, p))
        except Exception:
            return 0
        rows.sort(reverse=True)
        for _, p in rows[self.max_entries:]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        return len(rows)

    def describe(self, image_path: str, prompt: str = "") -> str:
        path = Path(str(image_path or ""))
        if not path.is_file():
            return ""
        key = self._hash(path)
        cached = self._cache_get(key)
        if cached:
            return cached
        if not self._ensure():
            return ""
        try:
            js = self._post(self.base + "/describe", {"image": str(path)}, self.timeout)
            text = str((js or {}).get("text") or "").strip()
        except Exception:
            text = ""
        if text:
            self._cache_put(key, text)
        return text
