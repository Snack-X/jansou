"""A reference client: joins a table and plays with a built-in jansou agent.

This is the template for a training client. The loop is: decode each ``event``
message and feed it to the agent's ``observe``, answer each ``decision``
message by picking one of the offered actions and replying with its index, and
print ``result``/``end`` reports. A learning agent replaces the built-in one
behind the same two calls.
"""

from __future__ import annotations

import argparse
import socket
import sys

from jansou.game.agents import (
    EfficiencyAgent,
    RandomAgent,
    SimpleAgent,
    SmartEfficiencyAgent,
)

from protocol import (
    ProtocolError,
    action_from_wire,
    decision_kind_from_wire,
    decode_event,
    read_message,
    send_message,
)

AGENTS = {
    "random": lambda seed: RandomAgent(seed),
    "simple": lambda seed: SimpleAgent(),
    "efficiency": lambda seed: EfficiencyAgent(seed),
    "smart": lambda seed: SmartEfficiencyAgent(seed),
}


def play(conn: socket.socket, agent, name: str, verbose: bool) -> None:
    """Join the table and answer messages until the session ends."""
    reader = conn.makefile("r", encoding="utf-8")
    send_message(conn, {"type": "join", "name": name})
    welcome = read_message(reader)
    if welcome["type"] != "welcome":
        raise ProtocolError(f"expected 'welcome', got {welcome['type']!r}")
    seat = welcome["seat"]
    print(
        f"joined as seat {seat}, {welcome['games']} game(s) of {welcome['player_count']} players"
    )

    while True:
        message = read_message(reader)
        if message["type"] == "event":
            if verbose:
                print(f"  {message['data']}")
            event = decode_event(message["data"])
            if event is not None:
                agent.observe(event)
        elif message["type"] == "decision":
            actions = [action_from_wire(data) for data in message["actions"]]
            kind = decision_kind_from_wire(message["kind"])
            chosen = agent.act(seat, kind, actions)
            send_message(conn, {"type": "action", "index": actions.index(chosen)})
        elif message["type"] == "result":
            print(
                f"game {message['game']}/{message['games']}: scores {message['scores']}"
            )
        elif message["type"] == "end":
            print("session over:")
            for entry in message["summary"]:
                print(
                    f"  {entry['name']:<20} avg placement {entry['average_placement']:.3f}  "
                    f"avg score {entry['average_score']:>9.1f}  firsts {entry['first_places']}"
                )
            return
        else:
            raise ProtocolError(f"unexpected message type {message['type']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Join a jansou table over a local socket."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="server address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=15151, help="server port (default: 15151)"
    )
    parser.add_argument(
        "--name", default=None, help="display name (default: the agent kind)"
    )
    parser.add_argument(
        "--agent",
        choices=sorted(AGENTS),
        default="smart",
        help="built-in agent (default: smart)",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="agent seed (default: unseeded)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print every event as it arrives"
    )
    args = parser.parse_args()

    agent = AGENTS[args.agent](args.seed)
    name = args.name or args.agent
    try:
        with socket.create_connection((args.host, args.port)) as conn:
            play(conn, agent, name, args.verbose)
    except (ProtocolError, ConnectionError, OSError) as error:
        print(f"client stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
