from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List

from .. import defaults


@dataclass
class KCField:
    """Sparse Kenyon-cell code: a cue activates a small, fixed fraction of KCs."""

    n: int = defaults.KC_SLOTS
    k: int = defaults.KC_ACTIVE

    def encode(self, cue: str) -> List[int]:
        if not cue:
            return []
        idx: List[int] = []
        i = 0
        while len(idx) < min(self.k, self.n):
            h = hashlib.sha1((cue + "|" + str(i)).encode("utf-8")).digest()
            v = int.from_bytes(h[:4], "little") % self.n
            if v not in idx:
                idx.append(v)
            i += 1
        return sorted(idx)
