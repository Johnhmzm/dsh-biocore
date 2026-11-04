from __future__ import annotations

from typing import Dict, List, Tuple

from .. import defaults
from ..connectome_prior import CONNECTOME
from ..digital_prior import FLY
from ..noise import clip
from .apl import APL
from .dan import DAN
from .habituation import Habituation
from .kc import KCField
from .mbon import MBON


class MushroomBody:
    """Drosophila mushroom body with two DAN/MBON channels.

    reward channel    : appetitive value (approach)
    punishment channel: aversive value (avoidance)
    value = reward - punishment
    """

    def __init__(
        self,
        n_kc: int = defaults.KC_SLOTS,
        k: int | None = None,
        lr: float = 0.05,
        punish_cross_lr: float = 0.5,
    ):
        k = FLY.compressed_kc(n_kc) if k is None else k
        self.kc = KCField(n=n_kc, k=k)
        self.mbon_reward = MBON(n_kc)
        self.mbon_punishment = MBON(n_kc)
        self.dan_reward = DAN()
        self.dan_punishment = DAN()
        self.apl = APL()
        self.hab = Habituation()
        self.lr = lr
        self.punish_cross_lr = punish_cross_lr

    def predict_channels(self, cue: str) -> Dict[str, object]:
        kc = self.kc.encode(cue)
        reward = self.mbon_reward.predict(kc)
        punishment = self.mbon_punishment.predict(kc)
        return {
            "reward": reward,
            "punishment": punishment,
            "value": reward - punishment,
            "kc": kc,
        }

    def predict(self, cue: str) -> Tuple[float, List[int]]:
        ch = self.predict_channels(cue)
        return float(ch["value"]), list(ch["kc"])  # type: ignore[arg-type]

    def learn(self, cue: str, feedback: float, dt: float = 60.0) -> Dict[str, object]:
        ch = self.predict_channels(cue)
        kc = list(ch["kc"])  # type: ignore[arg-type]
        reward_pred = float(ch["reward"])
        punishment_pred = float(ch["punishment"])

        # DAN compartments: appetitive and aversive are separate channels
        r_plus = max(0.0, feedback)
        r_minus = max(0.0, -feedback)
        dan_r = self.dan_reward.compute(r_plus, reward_pred)
        dan_p = self.dan_punishment.compute(r_minus, punishment_pred)

        # PPL1 punishment weakens the approach (reward) MBON in proportion to KC firing.
        dan_r_eff = dan_r - self.punish_cross_lr * r_minus

        gain = self.apl.step(kc, self.kc.n, dt=0.05)
        self.mbon_reward.learn(kc, dan_r_eff * gain, self.lr)
        self.mbon_punishment.learn(kc, dan_p * gain, self.lr)
        hab = self.hab.on_cue(cue, feedback, dt)

        return {
            "reward": reward_pred,
            "punishment": punishment_pred,
            "value": reward_pred - punishment_pred,
            "dan_reward": dan_r_eff,
            "dan_punishment": dan_p,
            "punish_cross_lr": self.punish_cross_lr,
            "habituation": hab,
            "kc": float(len(kc)),
        }

    def homeostatic(self, target: float = 20.0) -> None:
        """Synaptic scaling: keep total absolute weight bounded."""
        for mbon in (self.mbon_reward, self.mbon_punishment):
            total = sum(abs(w) for w in mbon.w)
            if total > target:
                scale = target / total
                mbon.w = [clip(w * scale, -1.0, 1.0) for w in mbon.w]

    def consolidate(self) -> None:
        self.mbon_reward.w = [clip(w * 0.995, -1.0, 1.0) for w in self.mbon_reward.w]
        self.mbon_punishment.w = [clip(w * 0.995, -1.0, 1.0) for w in self.mbon_punishment.w]
        self.homeostatic()

    def snapshot(self) -> Dict[str, object]:
        def nonzero(w: List[float]) -> int:
            return sum(1 for x in w if abs(x) > 1e-6)

        return {
            "prior": FLY.summary(),
            "n_kc": self.kc.n,
            "k": self.kc.k,
            "reward_nonzero": nonzero(self.mbon_reward.w),
            "punishment_nonzero": nonzero(self.mbon_punishment.w),
            "reward_sum": round(sum(self.mbon_reward.w), 4),
            "punishment_sum": round(sum(self.mbon_punishment.w), 4),
            "apl_activity": round(self.apl.activity, 4),
            "connectome": CONNECTOME.summary(),
            "habituation": {k: round(v, 3) for k, v in list(self.hab.state.items())[-8:]},
        }
