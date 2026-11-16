# -*- coding: utf-8 -*-
"""B13: the closed-loop gain is a dial, and its default stays 0.5 / 4."""
import math

from biocore_model import autonomy_loop as loop


def test_defaults_match_the_shipped_loop():
    assert loop.LOOP_GAIN == 0.5
    assert loop.LOOP_MIN_SEEN == 4


def test_gap_multiplier_no_effect_below_sample_floor():
    assert loop.gap_multiplier(3, 0.0, gain=1.0) == 1.0
    assert loop.gap_multiplier(3, 0.0, gain=0.5) == 1.0


def test_gap_multiplier_matches_the_old_formula_at_default_gain():
    for rate in (0.0, 0.25, 0.5, 1.0):
        assert round(loop.gap_multiplier(10, rate), 6) == round(
            1.0 + 0.5 * (1.0 - rate), 6)


def test_gap_multiplier_is_monotone_in_gain_and_rate():
    values = [loop.gap_multiplier(10, 0.5, gain=g) for g in (0.2, 0.5, 1.0)]
    assert values == sorted(values)
    assert loop.gap_multiplier(10, 0.0) >= loop.gap_multiplier(10, 1.0)


def test_breakpoint_probability_rises_with_gap_and_is_capped():
    a = loop.breakpoint_send(30, 600000)["p"]
    b = loop.breakpoint_send(240, 600000)["p"]
    c = loop.breakpoint_send(10000, 600000)["p"]
    assert a < b <= c <= loop.BP_CAP


def test_breakpoint_is_reproducible_within_the_same_minute():
    one = loop.breakpoint_send(180, 600500, seed_salt="s1")
    two = loop.breakpoint_send(180, 600900, seed_salt="s1")
    assert one["send"] == two["send"] and one["p"] == two["p"]
