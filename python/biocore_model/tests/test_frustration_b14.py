# -*- coding: utf-8 -*-
"""B14: frustration counts only when an expectation was not met (Amsel)."""
from biocore_model import frustration_curve as fc


def test_legacy_rule_counts_over_or_speak():
    assert fc.counts_as_attempt(over_threshold=True, spoke=False) is True
    assert fc.counts_as_attempt(over_threshold=False, spoke=True) is True
    assert fc.counts_as_attempt(over_threshold=False, spoke=False) is False


def test_expect_mode_ignores_bare_over_threshold():
    # "过线"只是状态不好，不是未兑现的预期
    assert fc.counts_as_attempt(over_threshold=True, spoke=False,
                                expect_mode=True) is False


def test_expect_mode_counts_intent_without_channel():
    assert fc.counts_as_attempt(over_threshold=False, spoke=True,
                                has_channel=False, expect_mode=True) is True


def test_expect_mode_does_not_count_a_sent_word():
    # 有通道 = 说出去了 = 预期兑现
    assert fc.counts_as_attempt(over_threshold=True, spoke=True,
                                has_channel=True, expect_mode=True) is False


def test_expectation_unmet_is_pure():
    assert fc.expectation_unmet(False, False, False) is False
    assert fc.expectation_unmet(True, True, True) is False
    assert fc.expectation_unmet(True, True, False) is True
