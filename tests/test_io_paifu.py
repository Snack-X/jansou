"""Tests for the format-neutral record and its replay."""

from __future__ import annotations

from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.io.paifu import (
    Agari,
    Call,
    Discard,
    Draw,
    Paifu,
    RoundLog,
    Ryuukyoku,
    leftover_deposits,
    replay_round,
    settled_final_scores,
    settled_scores,
)


def _t(name: str) -> Tile:
    return Tile(TileKind[name])


def _round(outcome, hands=None, events=(), riichi_sticks=0) -> RoundLog:
    return RoundLog(
        round_wind=Wind.EAST,
        dealer=0,
        honba=0,
        riichi_sticks=riichi_sticks,
        initial_dora=_t("S9"),
        scores=(25000, 25000, 25000, 25000),
        hands=hands or ((), (), (), ()),
        events=events,
        outcome=outcome,
    )


class TestReplay:
    def test_draw_returns_no_records(self) -> None:
        assert replay_round(_round(Ryuukyoku()), Rules(), 4) == []

    def test_explicit_hand_is_used_when_present(self) -> None:
        hand = Hand(
            (
                _t("M2"),
                _t("M3"),
                _t("M4"),
                _t("P3"),
                _t("P4"),
                _t("P5"),
                _t("P6"),
                _t("P7"),
                _t("P8"),
                _t("S2"),
                _t("S3"),
                _t("S4"),
                _t("S5"),
                _t("S5"),
            ),
        )
        agari = Agari(winner=0, from_seat=0, winning_tile=_t("M4"), fu=40, value=2000, hand=hand)
        records = replay_round(_round((agari,)), Rules(), 4)
        assert len(records) == 1
        assert records[0].hand is hand
        assert records[0].context.is_tsumo

    def test_ron_hand_appends_the_winning_tile(self) -> None:
        # No explicit hand: the winner's tracked concealed plus the ron tile.
        concealed = (
            _t("M2"),
            _t("M3"),
            _t("M4"),
            _t("P3"),
            _t("P4"),
            _t("P5"),
            _t("P6"),
            _t("P7"),
            _t("P8"),
            _t("S2"),
            _t("S3"),
            _t("S4"),
            _t("S5"),
        )
        agari = Agari(winner=1, from_seat=0, winning_tile=_t("S5"), value=1000)
        record = replay_round(_round((agari,), hands=((), concealed, (), ())), Rules(), 4)[0]
        assert not record.context.is_tsumo
        assert record.hand.concealed.count(_t("S5")) == 2  # the held one plus the ron tile

    def test_shouminkan_without_a_prior_pon_retires_only_the_added_tile(self) -> None:
        # A promotion with no matching pon to upgrade still consumes the added tile.
        kan = Meld(
            MeldType.SHOUMINKAN, (_t("EAST"),) * 4, called=_t("EAST"), source=CallSource.TOIMEN, added=_t("EAST")
        )
        round_log = RoundLog(
            round_wind=Wind.EAST,
            dealer=0,
            honba=0,
            riichi_sticks=0,
            initial_dora=_t("S9"),
            scores=(25000,) * 4,
            hands=((_t("EAST"),), (), (), ()),
            events=(Call(0, kan),),
            outcome=(Agari(winner=0, from_seat=0, winning_tile=_t("EAST"), value=8000),),
        )
        assert replay_round(round_log, Rules(), 4)[0].hand.melds == ()

    def test_ankan_call_takes_all_four_tiles_from_the_hand(self) -> None:
        # A closed kan names no called tile, so every tile comes out of the hand.
        ankan = Meld(MeldType.ANKAN, (_t("EAST"),) * 4)
        round_log = RoundLog(
            round_wind=Wind.EAST,
            dealer=0,
            honba=0,
            riichi_sticks=0,
            initial_dora=_t("S9"),
            scores=(25000,) * 4,
            hands=((_t("EAST"),) * 4, (), (), ()),
            events=(Call(0, ankan),),
            outcome=(Agari(winner=0, from_seat=0, winning_tile=_t("EAST"), value=8000),),
        )
        record = replay_round(round_log, Rules(), 4)[0]
        assert [meld.type for meld in record.hand.melds] == [MeldType.ANKAN]
        assert _t("EAST") not in record.hand.concealed

    def test_shouminkan_promotes_the_pon_past_other_melds(self) -> None:
        # Seat 0 holds a chi before its pon of East, so the promotion loop must
        # skip the chi to find the pon it upgrades.
        chi = Meld(MeldType.CHII, (_t("P1"), _t("P2"), _t("P3")), called=_t("P1"), source=CallSource.KAMICHA)
        pon = Meld(MeldType.PON, (_t("EAST"),) * 3, called=_t("EAST"), source=CallSource.TOIMEN)
        kan = Meld(
            MeldType.SHOUMINKAN, (_t("EAST"),) * 4, called=_t("EAST"), source=CallSource.TOIMEN, added=_t("EAST")
        )
        hands = ((_t("P2"), _t("P3"), _t("EAST"), _t("EAST")), (), (), ())
        events = (Call(0, chi), Call(0, pon), Draw(0, _t("EAST")), Call(0, kan))
        round_log = RoundLog(
            round_wind=Wind.EAST,
            dealer=0,
            honba=0,
            riichi_sticks=0,
            initial_dora=_t("S9"),
            scores=(25000,) * 4,
            hands=hands,
            events=events,
            outcome=(Agari(winner=0, from_seat=0, winning_tile=_t("EAST"), value=8000),),
        )
        melds = replay_round(round_log, Rules(), 4)[0].hand.melds
        assert [meld.type for meld in melds] == [MeldType.CHII, MeldType.SHOUMINKAN]


class TestPaifu:
    def test_holds_its_rounds(self) -> None:
        paifu = Paifu(rules=Rules(), player_count=4, rounds=(_round(Ryuukyoku()),))
        assert paifu.player_count == 4
        assert len(paifu.rounds) == 1


class TestScoreChain:
    def test_banked_riichi_and_draw_deltas_settle_the_scores(self) -> None:
        # Seat 0's riichi survived the ron window, so its deposit joins the pool.
        round_log = _round(
            Ryuukyoku(deltas=(1500, 1500, 1500, -4500)),
            events=(Draw(0, _t("P1")), Discard(0, _t("P1"), riichi=True)),
        )
        assert settled_scores(round_log) == (25500, 26500, 26500, 20500)
        assert leftover_deposits(round_log) == 1

    def test_a_win_sweeps_the_pool(self) -> None:
        # A multiple ron: every winner's deltas apply, and no deposit is left.
        first = Agari(winner=1, from_seat=0, winning_tile=_t("M1"), deltas=(-10000, 12000, 0, 0))
        second = Agari(winner=2, from_seat=0, winning_tile=_t("M1"), deltas=(-3900, 0, 3900, 0))
        round_log = _round((first, second), riichi_sticks=2)
        assert settled_scores(round_log) == (11100, 37000, 28900, 25000)
        assert leftover_deposits(round_log) == 0

    def test_a_ronned_riichi_discard_never_banks(self) -> None:
        events = (Discard(1, _t("M1"), riichi=True),)
        outcome = (Agari(winner=0, from_seat=1, winning_tile=_t("M1"), deltas=(3900, -3900, 0, 0)),)
        assert settled_scores(_round(outcome, events=events)) == (28900, 21100, 25000, 25000)

    def test_a_triple_ronned_riichi_discard_never_banks(self) -> None:
        events = (Discard(2, _t("M1"), riichi=True),)
        round_log = _round(Ryuukyoku(kind="ron3"), events=events, riichi_sticks=1)
        assert settled_scores(round_log) == (25000, 25000, 25000, 25000)
        assert leftover_deposits(round_log) == 1

    def test_riichi_banked_before_a_later_ron(self) -> None:
        # Seat 1's riichi discard passed; the win rides a later plain discard.
        events = (Discard(1, _t("M1"), riichi=True), Discard(3, _t("M2")))
        outcome = (Agari(winner=1, from_seat=3, winning_tile=_t("M2"), deltas=(0, 9000, 0, -8000)),)
        assert settled_scores(_round(outcome, events=events)) == (25000, 33000, 25000, 17000)

    def test_riichi_banked_before_a_tsumo(self) -> None:
        events = (Discard(1, _t("M1"), riichi=True), Draw(0, _t("M9")))
        outcome = (Agari(winner=0, from_seat=0, winning_tile=_t("M9"), deltas=(5000, -2000, -1000, -1000)),)
        assert settled_scores(_round(outcome, events=events)) == (30000, 22000, 24000, 24000)

    def test_settled_final_scores_award_leftover_deposits_to_first(self) -> None:
        last = _round(
            Ryuukyoku(deltas=(0, 0, 0, 0)),
            events=(Discard(0, _t("M1"), riichi=True),),
            riichi_sticks=1,
        )
        # Two sticks are left on the table; the rule sends them to seat 1, the first place.
        rules = Rules(leftover_deposits_to_first=True)
        assert settled_final_scores((last,), rules) == (24000, 27000, 25000, 25000)

    def test_settled_final_scores_discard_leftover_deposits_otherwise(self) -> None:
        last = _round(
            Ryuukyoku(deltas=(0, 0, 0, 0)),
            events=(Discard(0, _t("M1"), riichi=True),),
            riichi_sticks=1,
        )
        assert settled_final_scores((last,), Rules()) == (24000, 25000, 25000, 25000)
