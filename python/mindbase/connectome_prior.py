from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .digital_prior import FlyPrior


@dataclass(frozen=True)
class ConnectomePrior(FlyPrior):
    """Compressed structural prior from digital-fly connectomics.

    cell_types from MaleCNS 2026; MBON/DAN counts are approximate and used only
    as a scale, not as an exact reconstruction.
    """
    cell_types: int = 11_710
    mb_compartments: int = 15
    mbon_count: int = 34
    dan_count: int = 130

    def summary(self) -> Dict[str, object]:
        base = super().summary()
        base.update(
            {
                "cell_types": self.cell_types,
                "mb_compartments": self.mb_compartments,
                "mbon_count": self.mbon_count,
                "dan_count": self.dan_count,
                "note": "MBON/DAN counts are approximate, used as scale only",
            }
        )
        return base


CONNECTOME = ConnectomePrior()
