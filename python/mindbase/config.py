from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class MindConfig:
    """All tunable parameters of the base. Units are seconds unless noted."""

    seed: int = 42
    dt: float = 60.0

    # memory
    memory_capacity: int = 128
    memory_half_life: float = 30.0 * 86400.0  # 30 days, paper: power-law-ish decay
    memory_min_importance: float = 0.05
    # recency / importance / frequency / relevance
    memory_activation_weights: Tuple[float, float, float, float] = (0.35, 0.25, 0.20, 0.20)
    memory_retrieval: str = "bm25"  # bm25 | activation
    memory_affect_weight: float = 0.0  # 0 = off; affect-congruent retrieval bonus

    # working memory
    wm_capacity: int = 5
    wm_decay_tau: float = 300.0

    # affect / mood
    affect_tau: float = 20.0
    mood_tau: float = 900.0
    stress_tau: float = 300.0
    sleep_recovery_tau: float = 4 * 3600.0
    sleep_stress_floor: float = 0.05
    noise_sigma: float = 0.03

    # population clock: 2h / 12h / 48h
    clock_taus: Tuple[float, ...] = (2 * 3600.0, 12 * 3600.0, 48 * 3600.0)
    clock_weights: Tuple[float, ...] = (0.5, 0.3, 0.2)
    sleep_pressure_tau: float = 16 * 3600.0

    # autonomy / spontaneous action
    autonomy_tau: float = 900.0
    autonomy_threshold: float = 0.9
    autonomy_viability_target: float = 0.90
    autonomy_social_target: float = 0.50
    autonomy_activity_target: float = 0.30
    autonomy_adapt_lr: float = 0.004
    volition_temperature: float = 0.0  # 0 = deterministic; >0 = stochastic will
    energy_policy_threshold: float = 0.20
    uncertainty_gain: float = 0.0
    chaos_r: float = 3.9
    autonomy_cooldown: float = 180.0

    # meta / abstention
    abstain_threshold: float = 0.35

    # social
    social_tau: float = 600.0
    contagion_strength: float = 0.15

    # optional persona profile: Big Five + physiology/biology numbers
    persona_profile: Optional[Dict[str, float]] = None

    # trained heads (offline Appraisal / Policy / World)
    appraisal_blend: float = 0.35
    policy_prior_threshold: float = 0.55
    world_margin: float = 0.10
