"""Tests for the game environment: running, determinism, walls, recording."""

from __future__ import annotations

import random

import pytest

from jansou.core.rules import Rules
from jansou.core.tiles import full_tile_set
from jansou.game.actions import Tsumo
from jansou.game.agents import Agent, RandomAgent, SimpleAgent
from jansou.game.environment import Environment, GameConfigError, IllegalActionError
from jansou.game.events import DealStart, GameEnd, GameStart, Win


def _assert_conserved(scores: tuple[int, ...], start_total: int) -> None:
    # Points are conserved except for deposits discarded at game end (§19.4),
    # which leave the total short by a multiple of a thousand.
    leaked = start_total - sum(scores)
    assert leaked >= 0
    assert leaked % 1000 == 0


class TestRunning:
    def test_four_player_game_conserves_points(self) -> None:
        result = Environment(Rules(), seed=1).run([SimpleAgent() for _ in range(4)])
        _assert_conserved(result.scores, 100_000)
        assert set(result.ranking) == {0, 1, 2, 3}

    def test_random_agents_produce_a_result(self) -> None:
        result = Environment(Rules(), seed=2).run([RandomAgent(seat) for seat in range(4)])
        _assert_conserved(result.scores, 100_000)

    def test_three_player_game(self) -> None:
        result = Environment(Rules(player_count=3), seed=3).run([RandomAgent(seat) for seat in range(3)])
        _assert_conserved(result.scores, 3 * 25_000)

    def test_ranking_is_by_score(self) -> None:
        result = Environment(Rules(), seed=2).run([RandomAgent(seat) for seat in range(4)])
        ordered = [result.scores[seat] for seat in result.ranking]
        assert ordered == sorted(ordered, reverse=True)


class TestDeterminism:
    def test_same_seed_reproduces_the_game(self) -> None:
        first = Environment(Rules(), seed=99).run([RandomAgent(seat) for seat in range(4)])
        second = Environment(Rules(), seed=99).run([RandomAgent(seat) for seat in range(4)])
        assert first.scores == second.scores

    def test_predefined_walls_reproduce_without_a_seed(self) -> None:
        walls = _record_walls(seed=7)
        first = Environment(Rules(), walls=walls).run([RandomAgent(seat) for seat in range(4)])
        second = Environment(Rules(), walls=walls).run([RandomAgent(seat) for seat in range(4)])
        assert first.scores == second.scores


class TestRecording:
    def test_records_one_stream_per_deal(self) -> None:
        # Recording is unconditional: a default environment keeps every deal's stream.
        env = Environment(Rules(), seed=1)
        env.run([SimpleAgent() for _ in range(4)])
        assert env.records
        assert all(any(isinstance(event, DealStart) for event in deal) for deal in env.records)


class TestDecisionRecording:
    def test_off_by_default(self) -> None:
        env = Environment(Rules(), seed=1)
        env.run([SimpleAgent() for _ in range(4)])
        assert env.decisions == []

    def test_one_list_per_deal_parallel_to_records(self) -> None:
        env = Environment(Rules(), seed=1, record_decisions=True)
        env.run([SimpleAgent() for _ in range(4)])
        assert len(env.decisions) == len(env.records)
        assert all(deal for deal in env.decisions)

    def test_chosen_is_among_the_offered_actions(self) -> None:
        env = Environment(Rules(), seed=2, record_decisions=True)
        env.run([RandomAgent(seat) for seat in range(4)])
        for deal in env.decisions:
            for decision in deal:
                assert decision.chosen in decision.actions

    def test_event_index_anchors_a_record_prefix(self) -> None:
        env = Environment(Rules(), seed=2, record_decisions=True)
        env.run([RandomAgent(seat) for seat in range(4)])
        for events, deal in zip(env.records, env.decisions, strict=True):
            for decision in deal:
                # At least DealStart has been emitted before any decision.
                assert 1 <= decision.event_index <= len(events)

    def test_same_seed_reproduces_the_decisions(self) -> None:
        first = Environment(Rules(), seed=99, record_decisions=True)
        first.run([RandomAgent(seat) for seat in range(4)])
        second = Environment(Rules(), seed=99, record_decisions=True)
        second.run([RandomAgent(seat) for seat in range(4)])
        assert first.decisions == second.decisions


class TestStepwisePlay:
    def test_play_matches_run_exactly(self) -> None:
        # Driving play() with the same agents reproduces run(): same result,
        # byte-identical records, identical decisions.
        ran = Environment(Rules(), seed=42, record_decisions=True)
        ran_result = ran.run([RandomAgent(seat) for seat in range(4)])
        stepped = Environment(Rules(), seed=42, record_decisions=True)
        stepped_result = _drive(stepped, [RandomAgent(seat) for seat in range(4)])
        assert stepped_result == ran_result
        assert stepped.records == ran.records
        assert stepped.decisions == ran.decisions

    def test_interleaved_games_match_sequential_runs(self) -> None:
        sequential = [
            Environment(Rules(), seed=seed).run([RandomAgent(seat) for seat in range(4)]) for seed in range(3)
        ]
        games = {
            seed: (Environment(Rules(), seed=seed).play(), [RandomAgent(seat) for seat in range(4)])
            for seed in range(3)
        }
        requests = {seed: next(game) for seed, (game, _) in games.items()}
        interleaved: dict[int, object] = {}
        while games:
            for seed in list(games):  # one step of each game, round-robin
                game, agents = games[seed]
                request = requests[seed]
                action = agents[request.seat].act(request.seat, request.kind, list(request.actions))
                try:
                    requests[seed] = game.send(action)
                except StopIteration as stop:
                    interleaved[seed] = stop.value
                    del games[seed]
        assert [interleaved[seed] for seed in range(3)] == sequential

    def test_request_events_are_masked_for_the_deciding_seat(self) -> None:
        game = Environment(Rules(), seed=5).play()
        request = next(game)
        deal_start = next(event for event in request.events if isinstance(event, DealStart))
        assert deal_start.hands[request.seat] is not None
        assert all(deal_start.hands[seat] is None for seat in range(4) if seat != request.seat)

    def test_observe_sees_every_unmasked_event(self) -> None:
        seen: list = []
        env = Environment(Rules(), seed=5)
        game = env.play(observe=seen.append)
        request = next(game)
        agents = [SimpleAgent() for _ in range(4)]
        try:
            while True:
                request = game.send(agents[request.seat].act(request.seat, request.kind, list(request.actions)))
        except StopIteration:
            pass
        assert isinstance(seen[0], GameStart)
        assert isinstance(seen[-1], GameEnd)
        deal_events = [event for event in seen if not isinstance(event, (GameStart, GameEnd))]
        assert deal_events == [event for deal in env.records for event in deal]

    def test_foreign_action_raises_naming_the_seat(self) -> None:
        game = Environment(Rules(), seed=5).play()
        request = next(game)
        with pytest.raises(IllegalActionError, match=f"seat {request.seat}"):
            game.send(Tsumo())


def _drive(env: Environment, agents: list[Agent]) -> object:
    """Drive play() the way run() does, delivering observations per seat."""

    def fan_out(event: object) -> None:
        for seat, agent in enumerate(agents):
            agent.observe(event.mask_for(seat))  # type: ignore[attr-defined]

    game = env.play(observe=fan_out)
    request = next(game)
    while True:
        action = agents[request.seat].act(request.seat, request.kind, list(request.actions))
        try:
            request = game.send(action)
        except StopIteration as stop:
            return stop.value


class TestMaskingAndNotification:
    def test_agents_see_game_start_and_end(self) -> None:
        seen: list = []

        class Watcher(Agent):
            def observe(self, event: object) -> None:
                seen.append(event)

            def act(self, seat: int, kind: object, actions: list) -> object:
                _ = (seat, kind)
                return next(action for action in actions if action.__class__.__name__ in {"Discard", "Pass"})

        Environment(Rules(), seed=1).run([Watcher() for _ in range(4)])
        assert any(isinstance(event, GameStart) for event in seen)
        assert any(isinstance(event, GameEnd) for event in seen)

    def test_own_deal_hand_is_visible_others_are_masked(self) -> None:
        env = Environment(Rules(), seed=1)
        agent = _CapturingAgent()
        env.run([agent, SimpleAgent(), SimpleAgent(), SimpleAgent()])
        deal_start = agent.deal_start
        assert deal_start is not None
        assert deal_start.hands[0] is not None
        assert deal_start.hands[1] is None


class TestErrors:
    def test_wrong_agent_count(self) -> None:
        with pytest.raises(GameConfigError, match="agents"):
            Environment(Rules(), seed=1).run([SimpleAgent(), SimpleAgent()])

    def test_walls_run_out(self) -> None:
        walls = _record_walls(seed=7)[:1]
        with pytest.raises(GameConfigError, match="walls"):
            Environment(Rules(), walls=walls).run([RandomAgent(seat) for seat in range(4)])

    def test_illegal_action_is_rejected(self) -> None:
        class Cheater(Agent):
            def act(self, seat: int, kind: object, actions: list) -> object:
                _ = (seat, kind, actions)
                return Tsumo()  # never in the offered set on a fresh discard turn

        with pytest.raises(IllegalActionError, match="offered"):
            Environment(Rules(), seed=1).run([Cheater(), SimpleAgent(), SimpleAgent(), SimpleAgent()])


class _CapturingAgent(SimpleAgent):
    def __init__(self) -> None:
        super().__init__()
        self.deal_start: DealStart | None = None

    def observe(self, event: object) -> None:
        super().observe(event)
        if isinstance(event, DealStart) and self.deal_start is None:
            self.deal_start = event


def _record_walls(seed: int, count: int = 100) -> list:
    rng = random.Random(seed)
    walls = []
    for _ in range(count):
        tiles = full_tile_set(4, aka_dora=True)
        rng.shuffle(tiles)
        walls.append(tuple(tiles))
    return walls


def test_win_events_carry_results() -> None:
    env = Environment(Rules(), seed=1)
    env.run([SimpleAgent() for _ in range(4)])
    wins = [event for deal in env.records for event in deal if isinstance(event, Win)]
    for win in wins:
        assert win.result.han >= 0 or win.result.is_yakuman


def test_supplied_walls_play_a_full_game() -> None:
    # A hand-supplied list of full-set walls plays a game to its end.
    result = Environment(Rules(), walls=_record_walls(11)).run([SimpleAgent() for _ in range(4)])
    _assert_conserved(result.scores, 100_000)
