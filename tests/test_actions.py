"""Tests for action legality enumeration and the win-context builder."""

from __future__ import annotations

import pytest

from jansou.core.hand import CallSource, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind, full_tile_set
from jansou.game.actions import (
    AddedKan,
    Chii,
    ClosedKan,
    DeclareTenpai,
    Discard,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Riichi,
    Ron,
    Tsumo,
    discard_reactions,
    is_furiten,
    north_reactions,
    robbed_kan_reactions,
    self_actions,
    tenpai_declaration_options,
    win_context,
)
from jansou.game.state import Discard as DiscardMark
from jansou.game.state import GameState, PlayerState
from jansou.game.wall import Wall


def build(
    concealed: list[str],
    *,
    dealer: int = 0,
    player_count: int = 4,
    current: int = 0,
    drawn: str | None = None,
    rules: Rules | None = None,
    drain: int = 0,
    **overrides: object,
) -> GameState:
    wall = Wall(tuple(full_tile_set(player_count, aka_dora=False)))
    for _ in range(drain):
        wall.draw_live()
    players = [PlayerState(concealed=list(parse_mpsz(hand)) if hand else []) for hand in concealed]
    if drawn is not None:
        players[current].drawn = parse_mpsz(drawn)[0]
    state = GameState(
        rules=rules or Rules(player_count=player_count),
        scores=[25000] * player_count,
        wall=wall,
        dealer=dealer,
        round_wind=Wind.EAST,
        round_number=1,
        honba=0,
        deposit_pool=0,
        players=players,
        current_player=current,
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def _four(hand0: str, *, current: int = 0, **kw: object) -> GameState:
    return build([hand0, "", "", ""], current=current, **kw)


class TestSelfTurn:
    def test_tsumo_offered_on_a_winning_draw(self) -> None:
        state = _four("234m345m567p234s8s", drawn="8s")
        assert Tsumo() in self_actions(state)

    def test_riichi_offered_when_tenpai(self) -> None:
        state = _four("234m345m567p234s8s", drawn="9s")
        actions = self_actions(state)
        assert Riichi(Tile(TileKind.S9), tsumogiri=True) in actions
        assert Discard(Tile(TileKind.S9), tsumogiri=True) in actions

    def test_drawn_duplicate_is_a_separate_discard(self) -> None:
        state = _four("34555m567p789s22s", drawn="5m")
        actions = self_actions(state)
        assert Discard(Tile(TileKind.M5)) in actions
        assert Discard(Tile(TileKind.M5), tsumogiri=True) in actions

    def test_drawn_duplicate_is_a_separate_riichi(self) -> None:
        state = _four("234m345m567p234s9s", drawn="9s")
        actions = self_actions(state)
        assert Riichi(Tile(TileKind.S9)) in actions
        assert Riichi(Tile(TileKind.S9), tsumogiri=True) in actions

    def test_closed_kan_offered_with_four_copies(self) -> None:
        state = _four("1111m234p678p999s", drawn="5s")
        assert ClosedKan(TileKind.M1) in self_actions(state)

    def test_added_kan_offered_over_a_pon(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("222m")), called=Tile(TileKind.M2), source=CallSource.TOIMEN)
        state = _four("2m345p678p234s99s", drawn="1m")
        state.players[0].melds = [pon]
        assert AddedKan(Tile(TileKind.M2)) in self_actions(state)

    def test_nine_terminals_on_first_draw(self) -> None:
        state = _four("19m19p19s1234567z", drawn="1m")
        assert NineTerminals() in self_actions(state)

    def test_nine_terminals_gone_after_a_discard(self) -> None:
        state = _four("19m19p19s1234567z", drawn="1m")
        state.players[0].discards = [DiscardMark(tile=Tile(TileKind.M1))]
        assert NineTerminals() not in self_actions(state)

    def test_post_call_offers_only_discards_minus_restriction(self) -> None:
        state = _four("123m456p789p234s", current=0)
        state.players[0].drawn = None
        state.post_call_restriction = frozenset({TileKind.M1})
        actions = self_actions(state)
        assert all(isinstance(action, Discard) for action in actions)
        assert Discard(Tile(TileKind.M1)) not in actions
        assert Discard(Tile(TileKind.P4)) in actions

    def test_nuki_offered_in_sanma(self) -> None:
        state = build(["4z123p456p789p111s", "", ""], player_count=3, drawn="9p", rules=Rules(player_count=3))
        assert Nuki() in self_actions(state)


class TestRiichiLocked:
    def test_forced_tsumogiri_only(self) -> None:
        state = _four("555m123p456p789p2s", drawn="3m")
        state.players[0].riichi = True
        actions = self_actions(state)
        assert Discard(Tile(TileKind.M3), tsumogiri=True) in actions
        assert Discard(Tile(TileKind.S2)) not in actions

    def test_wait_preserving_closed_kan_offered(self) -> None:
        state = _four("555m123p456p789p2s", drawn="5m")
        state.players[0].riichi = True
        assert ClosedKan(TileKind.M5) in self_actions(state)

    def test_wait_changing_closed_kan_refused(self) -> None:
        state = _four("34555m567p789s22s", drawn="5m")
        state.players[0].riichi = True
        actions = self_actions(state)
        assert ClosedKan(TileKind.M5) not in actions
        assert Discard(Tile(TileKind.M5), tsumogiri=True) in actions


class TestReactions:
    def test_ron_offered_and_furiten_blocks(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""], last_discard=(0, Tile(TileKind.S8)))
        state.players[1].riichi = True
        assert Ron() in discard_reactions(state, 1, final=False)
        state.players[1].temporary_furiten = True
        assert discard_reactions(state, 1, final=False) == []

    def test_pon_combos_distinguish_red(self) -> None:
        state = build(["", "055s123m", "", ""], last_discard=(0, Tile(TileKind.S5)))
        actions = discard_reactions(state, 1, final=False)
        pons = [action for action in actions if isinstance(action, Pon)]
        assert len(pons) == 2  # (red, ordinary) and (ordinary, ordinary)
        assert Pass() in actions

    def test_chii_only_from_kamicha(self) -> None:
        state = build(["", "1245m", "", ""], last_discard=(0, Tile(TileKind.M3)))
        actions = discard_reactions(state, 1, final=False)
        chiis = [action for action in actions if isinstance(action, Chii)]
        assert len(chiis) == 3  # 123, 234, 345 pairings

    def test_chii_refused_from_non_kamicha(self) -> None:
        state = build(["", "1245m", "", ""], last_discard=(2, Tile(TileKind.M3)))
        assert not [action for action in discard_reactions(state, 1, final=False) if isinstance(action, Chii)]

    def test_open_kan(self) -> None:
        state = build(["", "555m123p", "", ""], last_discard=(0, Tile(TileKind.M5)))
        assert OpenKan() in discard_reactions(state, 1, final=False)

    def test_final_discard_is_ron_only(self) -> None:
        state = build(["", "555m123p", "", ""], last_discard=(0, Tile(TileKind.M5)), drain=122)
        actions = discard_reactions(state, 1, final=True)
        assert not [action for action in actions if isinstance(action, Pon)]

    def test_no_reaction_window(self) -> None:
        state = build(["", "123m456p789p22s", "", ""], last_discard=(0, Tile(TileKind.S9)))
        assert discard_reactions(state, 1, final=False) == []


class TestRobbingAndNorth:
    def test_chankan_ron(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""])
        state.players[1].riichi = True
        assert Ron() in robbed_kan_reactions(state, 1, Tile(TileKind.S8), added_kan=True)

    def test_closed_kan_kokushi_rob(self) -> None:
        rules = Rules(kokushi_ankan_chankan=True)
        state = build(["", "19m19p19s1234567z", "", ""], rules=rules)
        assert Ron() in robbed_kan_reactions(state, 1, Tile(TileKind.M1), added_kan=False)

    def test_closed_kan_rob_refused_without_flag(self) -> None:
        state = build(["", "19m19p19s1234567z", "", ""])
        assert robbed_kan_reactions(state, 1, Tile(TileKind.M1), added_kan=False) == []

    def test_north_ron_in_sanma(self) -> None:
        rules = Rules(player_count=3)
        state = build(["", "44z123p456p789p11s", ""], player_count=3, rules=rules)
        state.players[1].riichi = True
        assert Ron() in north_reactions(state, 1, Tile(TileKind.NORTH))


class TestWinContext:
    def test_haitei_on_the_last_draw(self) -> None:
        state = _four("234m345m567p234s8s", drawn="8s", drain=122)
        context = win_context(state, 0, is_tsumo=True)
        assert context.haitei

    def test_houtei_on_the_last_discard(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""], last_discard=(0, Tile(TileKind.S8)), drain=122)
        context = win_context(state, 1, is_tsumo=False)
        assert context.houtei

    def test_rinshan_excludes_haitei(self) -> None:
        state = _four("234m345m567p234s8s", drawn="8s", drain=122)
        context = win_context(state, 0, is_tsumo=True, rinshan=True)
        assert context.rinshan
        assert not context.haitei

    def test_tenhou_for_the_dealer(self) -> None:
        state = _four("234m345m567p234s8s", drawn="8s")
        context = win_context(state, 0, is_tsumo=True)
        assert context.tenhou
        assert not context.chiihou

    def test_chiihou_for_a_non_dealer(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""], current=1, drawn="8s")
        state.players[1].drawn = Tile(TileKind.S8)
        context = win_context(state, 1, is_tsumo=True)
        assert context.chiihou


class TestFuriten:
    def test_permanent_furiten_from_own_pile(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""])
        state.players[1].discards = [DiscardMark(tile=Tile(TileKind.S8))]
        assert is_furiten(state, 1)

    def test_riichi_furiten_mark(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""])
        state.players[1].riichi_furiten = True
        assert is_furiten(state, 1)

    def test_karaten_is_not_furiten(self) -> None:
        # A hand with all four of its only wait held cannot be furiten.
        state = build(["", "111m999m99p9999s", "", ""])
        state.players[1].discards = [DiscardMark(tile=Tile(TileKind.S9))]
        assert not is_furiten(state, 1)


class TestKanGating:
    def test_closed_kan_refused_at_the_cap(self) -> None:
        state = _four("1111m234p678p999s", drawn="5s", kans=4)
        assert ClosedKan(TileKind.M1) not in self_actions(state)

    def test_added_kan_refused_at_the_cap(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("222m")), called=Tile(TileKind.M2), source=CallSource.TOIMEN)
        state = _four("2m345p678p234s99s", drawn="1m", kans=4)
        state.players[0].melds = [pon]
        assert AddedKan(Tile(TileKind.M2)) not in self_actions(state)

    def test_open_kan_refused_at_the_cap(self) -> None:
        state = build(["", "555m123p", "", ""], last_discard=(0, Tile(TileKind.M5)), kans=4)
        assert OpenKan() not in discard_reactions(state, 1, final=False)

    def test_riichi_closed_kan_refused_when_flag_off(self) -> None:
        state = _four("555m123p456p789p2s", drawn="5m", rules=Rules(closed_kan_after_riichi=False))
        state.players[0].riichi = True
        assert ClosedKan(TileKind.M5) not in self_actions(state)


class TestRiichiGating:
    def test_riichi_locked_nuki_on_drawn_north(self) -> None:
        state = build(["123p456p789p1122s", "", ""], player_count=3, drawn="4z", rules=Rules(player_count=3))
        state.players[0].riichi = True
        assert Nuki() in self_actions(state)

    def test_riichi_refused_without_a_remaining_draw(self) -> None:
        state = _four("234m345m567p234s8s", drawn="9s", drain=119)
        assert not [action for action in self_actions(state) if isinstance(action, Riichi)]

    def test_riichi_without_tenpai_offers_every_tile(self) -> None:
        state = _four("123m456m11p22p33s5z", drawn="7z", rules=Rules(riichi_without_tenpai=True))
        assert [action for action in self_actions(state) if isinstance(action, Riichi)]


class TestReactionEdges:
    def test_reaction_without_a_discard_is_an_error(self) -> None:
        state = _four("123m456p789p234s99s")
        with pytest.raises(RuntimeError, match="no discard"):
            discard_reactions(state, 1, final=False)

    def test_pon_with_one_ordinary_and_one_red(self) -> None:
        state = build(["", "05s123p9m", "", ""], last_discard=(0, Tile(TileKind.S5)))
        pons = [action for action in discard_reactions(state, 1, final=False) if isinstance(action, Pon)]
        assert len(pons) == 1

    def test_chii_refused_on_an_honor(self) -> None:
        state = build(["", "1245m", "", ""], last_discard=(0, Tile(TileKind.EAST)))
        assert not [action for action in discard_reactions(state, 1, final=False) if isinstance(action, Chii)]

    def test_rob_refused_when_furiten(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""])
        state.players[1].riichi = True
        state.players[1].riichi_furiten = True
        assert robbed_kan_reactions(state, 1, Tile(TileKind.S8), added_kan=True) == []

    def test_rob_refused_on_a_non_winning_tile(self) -> None:
        state = build(["", "234m345m567p234s8s", "", ""])
        state.players[1].riichi = True
        assert robbed_kan_reactions(state, 1, Tile(TileKind.M1), added_kan=True) == []

    def test_north_reaction_declined_without_a_win(self) -> None:
        state = build(["", "234m345m567p234s8s", ""], player_count=3, rules=Rules(player_count=3))
        assert north_reactions(state, 1, Tile(TileKind.NORTH)) == []


def test_tenpai_declaration_options() -> None:
    options = tenpai_declaration_options()
    assert DeclareTenpai(declare=True) in options
    assert DeclareTenpai(declare=False) in options
