"""Tests for rules configuration and presets."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jansou.core.rules import PRESETS, Rules, preset, preset_name_of
from jansou.core.tiles import Wind


class TestDefaults:
    def test_baseline_defaults(self) -> None:
        rules = Rules()
        # Game-setup parameters.
        assert rules.player_count == 4
        assert rules.starting_points == 25_000
        assert rules.game_length is Wind.SOUTH
        assert rules.sudden_death_target == 30_000
        # Rule-flag spot checks.
        assert rules.honba_value == 100
        assert rules.honba_per_counter == 300  # one 100-point share per non-winner
        assert rules.double_wind_fu == 4
        assert not rules.kiriage_mangan
        assert rules.kazoe_yakuman
        assert rules.multiple_yakuman
        assert rules.double_yakuman
        assert rules.aka_dora
        assert not rules.nuki_dora
        assert not rules.kuikae_ban
        assert not rules.multiple_ron
        assert rules.abort_sanchahou
        assert rules.noten_penalty_pool == 3000
        assert not rules.formal_tenpai
        assert rules.ippatsu
        assert rules.dealer_repeat_on_tenpai
        assert rules.pao_daisangen
        assert not rules.pao_suukantsu
        assert rules.allow_negative_scores
        assert rules.agari_yame
        assert rules.sudden_death
        assert not rules.rank_ties_shared
        assert not rules.leftover_deposits_to_first

    def test_is_sanma(self) -> None:
        assert not Rules().is_sanma
        assert Rules(player_count=3).is_sanma


class TestValidation:
    def test_rejects_bad_player_count(self) -> None:
        with pytest.raises(ValueError, match="player count"):
            Rules(player_count=2)

    def test_rejects_nuki_dora_with_four_players(self) -> None:
        # A contradictory configuration, rejected at construction.
        with pytest.raises(ValueError, match="nuki dora"):
            Rules(nuki_dora=True)

    def test_nuki_dora_with_three_players_is_fine(self) -> None:
        assert Rules(player_count=3, nuki_dora=True).nuki_dora

    def test_rejects_bad_flag_values(self) -> None:
        with pytest.raises(ValueError, match="honba"):
            Rules(honba_value=300)
        with pytest.raises(ValueError, match="double-wind"):
            Rules(double_wind_fu=3)
        with pytest.raises(ValueError, match="penalty pool"):
            Rules(noten_penalty_pool=1500)


class TestPresets:
    def test_unknown_preset_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown preset 'wsop'"):
            preset("wsop")

    def test_all_presets_constructible(self) -> None:
        assert len(PRESETS) == 12
        for name in PRESETS:
            assert preset(name) == PRESETS[name]

    def test_preset_name_of_recovers_every_name(self) -> None:
        for name in PRESETS:
            assert preset_name_of(preset(name)) == name

    def test_preset_name_of_rejects_a_custom_configuration(self) -> None:
        assert preset_name_of(Rules(kiriage_mangan=True)) is None

    def test_association_base_via_renmei(self) -> None:
        rules = preset("renmei")
        assert not rules.pao_honba_to_liable  # JPML: the discarder pays the honba on a liable ron
        # Association base differences.
        assert rules.starting_points == 30_000
        assert rules.double_wind_fu == 2
        assert rules.kuikae_ban
        assert not rules.double_yakuman
        assert not rules.aka_dora
        assert not rules.abort_kyuushu_kyuuhai
        assert not rules.abort_suufon_renda
        assert not rules.abort_suucha_riichi
        assert not rules.abort_suukaikan
        assert not rules.abort_sanchahou
        assert rules.riichi_without_draw
        assert rules.formal_tenpai
        assert rules.tenpai_declaration
        assert not rules.nagashi_mangan
        assert not rules.agari_yame
        assert not rules.sudden_death
        assert rules.rank_ties_shared
        # Renmei's own overrides.
        assert not rules.ura_dora
        assert not rules.kan_dora
        assert not rules.ippatsu
        assert rules.pao_suukantsu

    def test_net_mahjong_base_via_tenhou(self) -> None:
        rules = preset("tenhou")
        # Net-mahjong base differences.
        assert not rules.open_kan_indicator_immediate
        assert rules.multiple_ron
        assert rules.kuikae_ban  # the correction note
        assert rules.formal_tenpai
        assert not rules.allow_negative_scores
        assert rules.leftover_deposits_to_first
        # Tenhou's own override.
        assert not rules.double_yakuman
        # Unnamed flags keep their defaults.
        assert rules.aka_dora
        assert rules.ippatsu
        assert rules.abort_sanchahou

    def test_mahjong_soul(self) -> None:
        rules = preset("mahjong-soul")
        assert not rules.abort_sanchahou
        assert rules.kokushi_ankan_chankan
        assert rules.double_yakuman  # not Tenhou's override

    def test_saikouisen_and_kyokai_differ_only_in_points(self) -> None:
        # Kyokai keeps the 25000 start; the rule flags match saikouisen.
        assert preset("kyokai") == replace(preset("saikouisen"), starting_points=25_000)

    def test_saikouisen_classic(self) -> None:
        rules = preset("saikouisen-classic")
        assert not rules.kuikae_ban  # even the identical tile may be swapped
        assert not rules.tenpai_declaration
        assert not rules.dealer_repeat_on_tenpai
        assert rules.riichi_without_tenpai

    def test_mu(self) -> None:
        rules = preset("mu")
        assert rules.honba_value == 0
        assert not rules.multiple_yakuman
        assert not rules.closed_kan_after_riichi
        assert not rules.kuikae_ban
        assert rules.noten_penalty_pool == 0
        assert rules.tenpai_declaration  # declaring decides dealer continuation

    def test_m_league(self) -> None:
        rules = preset("m-league")
        assert rules.kiriage_mangan
        assert not rules.kazoe_yakuman
        assert rules.aka_dora  # reverting the association base
        assert rules.pao_suukantsu
        assert rules.leftover_deposits_to_first
        assert rules.rank_ties_shared  # keeps the base's shared placement
        assert rules.starting_points == 25_000  # reverting the association base

    def test_saikyosen_reverts_tie_break(self) -> None:
        rules = preset("saikyosen")
        assert not rules.rank_ties_shared
        assert rules.starting_points == 30_000

    def test_three_player_presets_pin_setup(self) -> None:
        for name in ("tenhou-3p", "mahjong-soul-3p"):
            rules = preset(name)
            assert rules.player_count == 3
            assert rules.starting_points == 35_000
            assert rules.sudden_death_target == 40_000
            assert rules.nuki_dora
            assert not rules.abort_suucha_riichi
            assert not rules.abort_suufon_renda

    def test_tenhou_3p_keeps_tenhou_overrides(self) -> None:
        rules = preset("tenhou-3p")
        assert not rules.double_yakuman
        assert not rules.abort_sanchahou

    def test_mahjong_soul_3p_keeps_soul_overrides(self) -> None:
        rules = preset("mahjong-soul-3p")
        assert rules.kokushi_ankan_chankan
        assert not rules.abort_sanchahou

    def test_presets_are_replaceable(self) -> None:
        rules = replace(preset("tenhou"), starting_points=30_000)
        assert rules.starting_points == 30_000
        assert rules.multiple_ron
