"""Tests for tile efficiency: acceptance, discard evaluation, improvements."""

from __future__ import annotations

import pytest

from jansou.analysis.efficiency import acceptance, discard_evaluation, improvements
from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import TileKind


class TestAcceptance:
    def test_tenpai_acceptance_matches_waits_with_counts(self) -> None:
        hand = Hand(tuple(parse_mpsz("34555m567p789s22s")))
        accept = acceptance(hand)
        assert set(accept) == {TileKind.M2, TileKind.M5, TileKind.S2}
        assert accept[TileKind.M2] == 4  # none in hand
        assert accept[TileKind.M5] == 1  # three 5m already held
        assert accept[TileKind.S2] == 2  # two 2s already held

    def test_visible_tiles_reduce_counts(self) -> None:
        hand = Hand(tuple(parse_mpsz("34555m567p789s22s")))
        visible = [0] * 34
        visible[TileKind.M2] = 2  # two 2m already discarded elsewhere
        accept = acceptance(hand, visible=visible)
        assert accept[TileKind.M2] == 2

    def test_kind_with_no_copies_left_is_dropped(self) -> None:
        hand = Hand(tuple(parse_mpsz("34555m567p789s22s")))
        visible = [0] * 34
        visible[TileKind.M2] = 4  # all 2m accounted for
        assert TileKind.M2 not in acceptance(hand, visible=visible)

    def test_rejects_a_post_draw_hand(self) -> None:
        # Acceptance draws onto a resting hand; probing past the holding size is an error.
        hand = Hand(tuple(parse_mpsz("34555m567p789s223s")))
        with pytest.raises(ValueError, match="concealed tiles"):
            acceptance(hand)


class TestDiscardEvaluation:
    def test_orders_by_shanten_then_acceptance(self) -> None:
        hand = Hand(tuple(parse_mpsz("123m456m789m13p688s")))  # 14 tiles, post-draw
        options = discard_evaluation(hand)
        # Ordered by resulting shanten ascending, then total acceptance descending,
        # ties by ascending discard kind.
        keys = [(option.shanten, -option.total_acceptance, option.discard) for option in options]
        assert keys == sorted(keys)

    def test_prefers_lower_shanten_over_higher_acceptance(self) -> None:
        # Discarding an isolated 9m leaves shanten 4 with a wide but useless
        # acceptance; discarding a lone honor/terminal drops to shanten 3. The
        # closer-to-ready discard must rank first despite its smaller acceptance.
        hand = Hand(parse_mpsz("279m 1569p 1168s 35z 2m"))
        options = discard_evaluation(hand)
        best = options[0]
        assert best.shanten == min(option.shanten for option in options)
        assert any(
            option.shanten > best.shanten and option.total_acceptance > best.total_acceptance for option in options
        )

    def test_reports_resulting_shanten(self) -> None:
        hand = Hand(tuple(parse_mpsz("123m456m789m13p688s")))
        options = discard_evaluation(hand)
        best = options[0]
        assert best.shanten >= 0
        assert best.total_acceptance == sum(best.acceptance.values())

    def test_rejects_non_post_draw_size(self) -> None:
        hand = Hand(tuple(parse_mpsz("34555m567p789s22s")))  # 13 tiles
        with pytest.raises(ValueError, match="post-draw"):
            discard_evaluation(hand)


class TestImprovements:
    def test_acceptance_upgrade_example(self) -> None:
        # 123m 456p 789p 35s 99s waits only on 4s (kanchan). Drawing 6s and
        # discarding 3s widens the wait to 4s/7s.
        hand = Hand(tuple(parse_mpsz("123m456p789p35s99s")))
        result = improvements(hand)
        assert TileKind.S6 in result
        assert TileKind.S7 in result[TileKind.S6]
        assert TileKind.S4 in result[TileKind.S6]

    def test_no_improvement_when_already_widest(self) -> None:
        # A clean ryanmen tenpai has no shape upgrade available.
        hand = Hand(tuple(parse_mpsz("123m456m789m56p99s")))
        assert improvements(hand) == {}

    def test_exhausted_draw_is_skipped(self) -> None:
        # A kind with every copy visible elsewhere is never an improvement draw.
        hand = Hand(tuple(parse_mpsz("123m456p789p35s99s")))
        visible = [0] * 34
        visible[TileKind.S7] = 4  # all 7s accounted for
        result = improvements(hand, visible=visible)
        assert TileKind.S7 not in result
