from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from . import defaults


@dataclass(frozen=True)
class FlyPrior:
    """Structural priors from the digital-fly literature.

    Numbers are from FlyWire / MaleCNS; the base is a compressed functional
    model, not a spiking whole-brain simulation.
    """
    neurons: int = defaults.NEURONS
    synapses: int = defaults.SYNAPSES
    kc_count: int = defaults.KC_COUNT
    kc_sparsity: float = defaults.KC_SPARSITY
    compression_budget: int = defaults.KC_SLOTS

    def compressed_kc(self, n_kc: int | None = None) -> int:
        n = n_kc or self.compression_budget
        return max(1, round(n * self.kc_sparsity))

    def summary(self) -> Dict[str, object]:
        return {
            "source": "FlyWire/MaleCNS (digital fly)",
            "neurons": self.neurons,
            "synapses": self.synapses,
            "kc_count": self.kc_count,
            "kc_sparsity": self.kc_sparsity,
            "compression_budget": self.compression_budget,
        }


FLY = FlyPrior()
