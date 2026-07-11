"""A human-playable terminal client.

Joins a table like any client, but draws the table after every event and asks
you to pick each action by number. Opponents' hands stay hidden — you see
exactly what your seat is allowed to see.
"""

from __future__ import annotations

import argparse
import select
import socket
import sys

from protocol import ProtocolError, read_message, send_message
from table import TableView, color_enabled, describe_action, menu_order, render

_PROMPTS = {
    "self": "your turn",
    "discard_reaction": "react to the discard",
    "robbed_kan": "the kan can be robbed",
    "north_reaction": "react to the extracted north",
    "tenpai": "exhaustive draw",
}


def redraw(view: TableView, seat: int, *, color: bool) -> None:
    """Clear the screen (on a terminal) and draw the table."""
    if color:
        sys.stdout.write("\x1b[H\x1b[J")
    print(render(view, viewpoint=seat, color=color))


def prompt_line(conn: socket.socket, prompt: str) -> str:
    """Read a line of input while also noticing the server hanging up.

    Waits on stdin and the socket together. While no data is pending from the
    server, its socket turning readable can only mean EOF (during a prompt the
    server sits waiting on this client); once data is queued the connection is
    known alive and the wait falls back to stdin alone — the main loop reads
    the queued messages after the prompt.
    """
    print(prompt, end="", flush=True)
    watched = [sys.stdin, conn]
    while True:
        readable, _, _ = select.select(watched, [], [])
        if conn in readable:
            if conn.recv(1, socket.MSG_PEEK) == b"":
                print()
                raise ProtocolError("server closed the connection")
            watched = [sys.stdin]
            continue
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line


def pause(conn: socket.socket) -> None:
    prompt_line(conn, "(enter to continue) ")


def choose(view: TableView, message: dict, conn: socket.socket, *, color: bool) -> int:
    """Show the offered actions and read a valid pick, returning its index."""
    heading = _PROMPTS.get(message["kind"], message["kind"])
    if message["kind"] != "self" and view.last_discard is not None:
        discarder, token = view.last_discard
        heading += f" ({view.names[discarder]} discarded {token})"
    print(f"{heading}:")
    actions = message["actions"]
    order = menu_order(actions)
    for number, index in enumerate(order, 1):
        print(f"  {number}) {describe_action(actions[index], color=color)}")
    while True:
        raw = prompt_line(conn, "> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(actions):
            return order[int(raw) - 1]
        print(f"enter a number between 1 and {len(actions)}")


def play(conn: socket.socket, name: str, *, color: bool) -> None:
    """Join the table and play until the session ends."""
    # Unbuffered, so no message can hide in a userspace buffer: prompt_line's
    # select-and-peek on the raw socket then sees exactly what is pending.
    reader = conn.makefile("rb", buffering=0)
    send_message(conn, {"type": "join", "name": name})
    welcome = read_message(reader)
    if welcome["type"] != "welcome":
        raise ProtocolError(f"expected 'welcome', got {welcome['type']!r}")
    seat = welcome["seat"]
    print(
        f"joined as seat {seat}, {welcome['games']} game(s) of {welcome['player_count']} players"
    )

    view = TableView()
    while True:
        message = read_message(reader)
        if message["type"] == "event":
            data = message["data"]
            view.apply(data)
            redraw(view, seat, color=color)
            if data["type"] in ("win", "ryuukyoku"):
                pause(conn)
        elif message["type"] == "decision":
            redraw(view, seat, color=color)
            send_message(
                conn, {"type": "action", "index": choose(view, message, conn, color=color)}
            )
        elif message["type"] == "result":
            standing = ", ".join(
                f"{view.names[s]} {message['scores'][s]}" for s in message["ranking"]
            )
            print(f"game {message['game']}/{message['games']}: {standing}")
            if message["game"] < message["games"]:
                pause(conn)
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
        description="Play at a jansou table from the terminal."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="server address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=15151, help="server port (default: 15151)"
    )
    parser.add_argument("--name", default="human", help="display name (default: human)")
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="plain text, no ANSI colors or screen clearing",
    )
    args = parser.parse_args()

    color = color_enabled() and not args.no_color
    try:
        with socket.create_connection((args.host, args.port)) as conn:
            play(conn, args.name, color=color)
    except (KeyboardInterrupt, EOFError):
        print("\nleft the table")
        return 1
    except (ProtocolError, ConnectionError, OSError) as error:
        print(f"client stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
