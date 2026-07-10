"""Scoring a parsed game against the values its log recorded.

Each win is rebuilt into an `AgariRecord` by the replay, scored with the
library, and checked against the log: the rounded fu and, decisively, the win
value -- the points the win moves before honba and deposits. Reproducing that
value confirms the han total, the fu, the limit tier, and the dealer and
self-draw multipliers all at once. A round that our scorer cannot score, or
that reaches a different value, is reported rather than raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.io.paifu import Paifu, replay_round
from jansou.scoring.score import ScoringError, score

if TYPE_CHECKING:
    from jansou.io.paifu import AgariRecord

_CHIITOITSU_FU = 25


@dataclass(frozen=True)
class Verdict:
    """The outcome of checking one win: passed, or why it failed.

    Attributes:
        winner: The seat index of the player who won the checked hand.
        passed: Whether the win reproduced the value (and fu) the log recorded.
        detail: A human-readable reason when ``passed`` is false, empty otherwise.
    """

    winner: int
    passed: bool
    detail: str = ""


def check_win(record: AgariRecord) -> Verdict:
    """Score one rebuilt win and compare it to the value the log recorded.

    The win is scored with the library, and its win value -- the points moved
    before honba and deposits -- is compared to the recorded value; the rounded
    fu is compared too, allowing the log's fixed-fu conventions, but not for a
    yakuman, whose value does not depend on fu. A hand the scorer cannot score
    fails rather than raising.

    Args:
        record: The rebuilt ``AgariRecord`` for one win: its hand, winning tile,
            scoring context, and the expected value and fu from the log.

    Returns:
        A passing ``Verdict``, or a failing one whose ``detail`` names the
        mismatch (unscorable, value differs, or fu differs).
    """
    try:
        result = score(record.hand, record.winning_tile, record.context)
    except ScoringError as error:
        return Verdict(record.winner, passed=False, detail=f"unscorable: {error}")
    value = result.payment.total - result.payment.honba - result.payment.sticks
    if record.expected_value is not None and value != record.expected_value:
        return Verdict(
            record.winner,
            passed=False,
            detail=f"value {value} != expected {record.expected_value}",
        )
    # A yakuman's value ignores fu, and logs record it inconsistently there, so fu is only checked otherwise.
    check_fu = record.expected_fu is not None and not result.is_yakuman
    if check_fu and not _fu_matches(result.fu.total, record.expected_fu):
        return Verdict(
            record.winner,
            passed=False,
            detail=f"fu {result.fu.total} != expected {record.expected_fu}",
        )
    return Verdict(record.winner, passed=True)


def _fu_matches(computed: int, expected: int) -> bool:
    """Whether computed fu agrees, allowing the log's fixed-fu conventions."""
    # A kazoe yakuman reports 0 fu; chiitoitsu reports 25. (True yakuman skip fu in the caller.)
    return expected in (0, _CHIITOITSU_FU) or computed == expected


def check_paifu(paifu: Paifu) -> list[Verdict]:
    """Check every win in a parsed game.

    Each round is replayed into its wins, and each win is scored and compared to
    the log via ``check_win``.

    Args:
        paifu: The parsed game, carrying its rounds, rules, and player count.

    Returns:
        One ``Verdict`` per win, in round-then-win order.
    """
    return [
        check_win(record)
        for round_log in paifu.rounds
        for record in replay_round(round_log, paifu.rules, paifu.player_count)
    ]
