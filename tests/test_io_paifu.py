"""Tests for the format-neutral record and its replay."""

from __future__ import annotations

from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.io.paifu import Agari, Call, Draw, Paifu, RoundLog, Ryuukyoku, replay_round


def _t(name: str) -> Tile:
    return Tile(TileKind[name])


def _round(outcome, hands=None) -> RoundLog:
    return RoundLog(
        round_wind=Wind.EAST,
        dealer=0,
        honba=0,
        riichi_sticks=0,
        initial_dora=_t("S9"),
        scores=(25000, 25000, 25000, 25000),
        hands=hands or ((), (), (), ()),
        events=(),
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
