"""A local-socket mahjong table built on the jansou engine.

The server opens one table: remote clients join over TCP and take seats in
connection order, built-in jansou agents fill the remaining seats, and the
requested number of games is played back to back on the same connections.
Every event is streamed to every seat (masked per seat, as in live play), each
decision is sent to the deciding seat as an enumerated list of legal actions,
and the seat answers with an index. Per-game results and a final placement
summary go both to the clients and to stdout.

Decisions in a mahjong game are strictly sequential, so the server is a plain
synchronous loop: no concurrency, one blocking read per decision.
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time

from jansou.core.rules import PRESETS, Rules, preset
from jansou.game.agents import (
    EfficiencyAgent,
    RandomAgent,
    SimpleAgent,
    SmartEfficiencyAgent,
)
from jansou.game.environment import (
    DecisionRequest,
    Environment,
    GameResult,
    IllegalActionError,
)

from protocol import (
    ProtocolError,
    action_to_wire,
    decision_kind_to_wire,
    encode_event,
    read_message,
    send_message,
)
from table import TableView, color_enabled, render

#: Starting seat winds, for the seating announcement.
_SEAT_WINDS = ("E", "S", "W", "N")

#: Builders for the agents that fill seats no client took.
FILL_AGENTS = {
    "random": lambda seed: RandomAgent(seed),
    "simple": lambda seed: SimpleAgent(),
    "efficiency": lambda seed: EfficiencyAgent(seed),
    "smart": lambda seed: SmartEfficiencyAgent(seed),
}


class RemoteSeat:
    """A seat played by a connected client."""

    def __init__(self, conn: socket.socket, name: str) -> None:
        self.conn = conn
        self.reader = conn.makefile("r", encoding="utf-8")
        self.name = name

    def observe(self, event) -> None:
        send_message(self.conn, {"type": "event", "data": encode_event(event)})

    def act(self, request: DecisionRequest):
        send_message(
            self.conn,
            {
                "type": "decision",
                "kind": decision_kind_to_wire(request.kind),
                "actions": [action_to_wire(action) for action in request.actions],
            },
        )
        reply = read_message(self.reader)
        if reply["type"] != "action":
            raise ProtocolError(
                f"{self.name}: expected an 'action' reply, got {reply['type']!r}"
            )
        index = reply.get("index")
        if not isinstance(index, int) or not 0 <= index < len(request.actions):
            raise ProtocolError(
                f"{self.name}: action index {index!r} is not in 0..{len(request.actions) - 1}"
            )
        return request.actions[index]

    def send(self, message: dict) -> None:
        send_message(self.conn, message)

    def close(self) -> None:
        self.conn.close()


class LocalSeat:
    """A seat played by a built-in agent on the server."""

    def __init__(self, agent, name: str) -> None:
        self.agent = agent
        self.name = name

    def observe(self, event) -> None:
        self.agent.observe(event)

    def act(self, request: DecisionRequest):
        return self.agent.act(request.seat, request.kind, list(request.actions))

    def send(self, message: dict) -> None:
        pass

    def close(self) -> None:
        pass


class LiveDisplay:
    """A spectator view on the server terminal, redrawn after every event.

    Sees the unmasked stream, so every hand is open; deal outcomes linger
    on screen a little longer than ordinary events.
    """

    OUTCOME_LINGER = 10  # in units of the per-event delay

    def __init__(self, delay: float) -> None:
        self.view = TableView()
        self.delay = delay
        self.color = color_enabled()

    def show(self, data: dict) -> None:
        self.view.apply(data)
        if self.color:
            sys.stdout.write("\x1b[H\x1b[J")
        print(render(self.view, color=self.color), flush=True)
        linger = self.OUTCOME_LINGER if data["type"] in ("win", "ryuukyoku") else 1
        time.sleep(self.delay * linger)


def accept_clients(host: str, port: int, count: int) -> list[RemoteSeat]:
    """Wait for the requested number of clients; seats are drawn after everyone joins."""
    clients: list[RemoteSeat] = []
    if count == 0:
        return clients
    with socket.create_server((host, port)) as listener:
        print(f"listening on {host}:{port}, waiting for {count} client(s)")
        for client_index in range(count):
            conn, address = listener.accept()
            client = RemoteSeat(conn, f"client-{client_index + 1}")
            join = read_message(client.reader)
            if join["type"] != "join":
                raise ProtocolError(f"expected a 'join' message, got {join['type']!r}")
            client.name = str(join.get("name") or client.name)
            clients.append(client)
            print(f"{client.name} joined from {address[0]}:{address[1]}")
    return clients


def play_game(
    rules: Rules,
    seats: list,
    seed: int | None,
    verbose: bool,
    display: LiveDisplay | None,
) -> GameResult:
    """Run one game, streaming masked events to every seat."""
    env = Environment(rules, seed=seed)
    names = tuple(seat.name for seat in seats)

    def fan_out(event) -> None:
        if verbose:
            print(f"  {encode_event(event)}")
        # Deliver to the seats first: the display sleeps, and clients must not
        # learn of an event later than the server screen does.
        for seat_index, seat in enumerate(seats):
            seat.observe(event.mask_for(seat_index))
        if display is not None:
            display.show(encode_event(event))

    game = env.play(names, observe=fan_out)
    request = next(game)
    while True:
        action = seats[request.seat].act(request)
        try:
            request = game.send(action)
        except StopIteration as stop:
            return stop.value


def play_session(
    rules: Rules,
    seats: list,
    games: int,
    seed: int | None,
    verbose: bool,
    display: LiveDisplay | None,
) -> None:
    """Play the games back to back and report per-game and final standings."""
    placements = [[] for _ in seats]
    final_scores = [[] for _ in seats]
    for game_index in range(games):
        game_seed = None if seed is None else seed + game_index
        result = play_game(rules, seats, game_seed, verbose, display)
        for place, seat_index in enumerate(result.ranking):
            placements[seat_index].append(place + 1)
        for seat_index, score in enumerate(result.scores):
            final_scores[seat_index].append(score)
        report = {
            "type": "result",
            "game": game_index + 1,
            "games": games,
            "scores": list(result.scores),
            "ranking": list(result.ranking),
        }
        for seat in seats:
            seat.send(report)
        standing = ", ".join(
            f"{seats[s].name} {result.scores[s]}" for s in result.ranking
        )
        print(f"game {game_index + 1}/{games}: {standing}")
        if display is not None:
            time.sleep(display.delay * LiveDisplay.OUTCOME_LINGER)

    summary = [
        {
            "seat": seat_index,
            "name": seat.name,
            "average_placement": round(sum(placements[seat_index]) / games, 3),
            "average_score": round(sum(final_scores[seat_index]) / games, 1),
            "first_places": placements[seat_index].count(1),
        }
        for seat_index, seat in enumerate(seats)
    ]
    for seat in seats:
        seat.send({"type": "end", "games": games, "summary": summary})
    print(f"\nsession over ({games} game(s)):")
    for entry in sorted(summary, key=lambda entry: entry["average_placement"]):
        print(
            f"  {entry['name']:<20} avg placement {entry['average_placement']:.3f}  "
            f"avg score {entry['average_score']:>9.1f}  firsts {entry['first_places']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host a jansou table over a local socket."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=15151, help="port to listen on (default: 15151)"
    )
    parser.add_argument(
        "--clients", type=int, default=1, help="remote seats to wait for (default: 1)"
    )
    parser.add_argument(
        "--fill",
        choices=sorted(FILL_AGENTS),
        default="smart",
        help="built-in agent for the remaining seats (default: smart)",
    )
    parser.add_argument(
        "--games", type=int, default=1, help="games to play back to back (default: 1)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="base seed; game i uses seed+i (default: unseeded)",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=None,
        help="rules preset (default: baseline)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print every event as it happens"
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="draw the table live (unmasked) after every event",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="seconds per event with --display (default: 0.15)",
    )
    args = parser.parse_args()

    rules = preset(args.preset) if args.preset else Rules()
    if not 0 <= args.clients <= rules.player_count:
        parser.error(
            f"--clients must be between 0 and {rules.player_count} for these rules"
        )

    seats: list = accept_clients(args.host, args.port, args.clients)
    for fill_index in range(rules.player_count - args.clients):
        agent_seed = None if args.seed is None else args.seed * 1000 + fill_index
        seats.append(
            LocalSeat(
                FILL_AGENTS[args.fill](agent_seed), f"{args.fill}-{fill_index + 1}"
            )
        )

    # Draw seats at random (reproducibly under --seed), then tell each client its seat.
    random.Random(args.seed).shuffle(seats)
    for seat_index, seat in enumerate(seats):
        seat.send(
            {
                "type": "welcome",
                "seat": seat_index,
                "player_count": rules.player_count,
                "games": args.games,
            }
        )
        print(f"seat {seat_index} ({_SEAT_WINDS[seat_index]}): {seat.name}")

    display = LiveDisplay(args.delay) if args.display else None
    try:
        play_session(rules, seats, args.games, args.seed, args.verbose, display)
    except (ProtocolError, IllegalActionError, ConnectionError, OSError) as error:
        print(f"session aborted: {error}", file=sys.stderr)
        return 1
    finally:
        for seat in seats:
            seat.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
