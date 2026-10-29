"""Trusted-board validation and one-ply transition resolution.

Screen recognition is noisy.  Nothing in this module performs input; it turns a
raw recognition result into a position that is safe for the controller to use.
"""
from __future__ import annotations

from collections import Counter

from board import Board, RED, BLACK, color_of

MAX_COUNTS = {"K": 1, "A": 2, "B": 2, "N": 2, "R": 2, "C": 2, "P": 5}


def temporal_occupancy_consensus(frames: list[list[str | None]]) -> list[str | None] | None:
    """Return a per-square majority from the latest three occupancy frames.

    Occupancy frames use ``R``/``r`` as side placeholders.  A one-frame JJ
    highlight, animation gap or template miss therefore cannot erase a trusted
    piece.  Until three complete frames exist, the newest frame is returned so
    startup and recovery are not artificially delayed.
    """
    usable = [frame for frame in frames[-3:] if len(frame) == 90]
    if not usable:
        return None
    if len(usable) < 3:
        return usable[-1][:]
    result = []
    for index in range(90):
        values = [frame[index] for frame in usable]
        winner = next((value for value in (None, "R", "r")
                       if values.count(value) >= 2), values[-1])
        result.append(winner)
    return result


def position_errors(squares: list[str | None]) -> list[str]:
    """Return structural errors that prove a recognition result is impossible."""
    errors: list[str] = []
    if len(squares) != 90:
        return [f"expected 90 squares, got {len(squares)}"]
    allowed = set("KABNRCPkabnrcp")
    unknown = sorted({p for p in squares if p is not None and p not in allowed})
    if unknown:
        errors.append("unknown pieces: " + ",".join(unknown))
    counts = Counter(p for p in squares if p is not None)
    for side_name, upper in (("red", True), ("black", False)):
        king = "K" if upper else "k"
        if counts[king] != 1:
            errors.append(f"{side_name} king count is {counts[king]}")
        for kind, maximum in MAX_COUNTS.items():
            code = kind if upper else kind.lower()
            if counts[code] > maximum:
                errors.append(f"too many {code}: {counts[code]} > {maximum}")
    # 兵卒不能后退，过河前也不能横走。高亮经常把马/炮误识别成兵卒；
    # 这类局面虽然数量正常，却会被 Pikafish 作为 unsupported position 拒绝。
    for index, piece in enumerate(squares):
        row, column = divmod(index, 9)
        if piece == "P":
            if row > 6:
                errors.append(f"red pawn on unreachable square: row={row}, col={column}")
            elif row >= 5 and column % 2 == 1:
                errors.append(f"red pawn moved sideways before crossing: row={row}, col={column}")
        elif piece == "p":
            if row < 3:
                errors.append(f"black pawn on unreachable square: row={row}, col={column}")
            elif row <= 4 and column % 2 == 1:
                errors.append(f"black pawn moved sideways before crossing: row={row}, col={column}")
    return errors


def is_plausible(squares: list[str | None]) -> bool:
    return not position_errors(squares)


def same_position(left: list[str | None], right: list[str | None]) -> bool:
    return len(left) == len(right) == 90 and left == right


def trusted_position_matches(expected: list[str | None], observed: list[str | None],
                             tolerant_indices=()) -> bool:
    """Compare with trusted state while allowing moved-piece glyph variants.

    JJ may render a moved piece with a different Chinese glyph.  On explicitly
    tracked squares we still require occupancy and side/color to match.
    """
    if len(expected) != 90 or len(observed) != 90:
        return False
    tolerant = set(tolerant_indices)
    for index, (wanted, seen) in enumerate(zip(expected, observed)):
        if wanted == seen:
            continue
        if (index not in tolerant or wanted is None or seen is None or
                color_of(wanted) != color_of(seen)):
            return False
    return True


def occupancy_difference_count(expected: list[str | None],
                               observed: list[str | None]) -> int:
    """Count square-level occupancy/side differences, ignoring glyph names."""
    if len(expected) != 90 or len(observed) != 90:
        return 90
    differences = 0
    for wanted, seen in zip(expected, observed):
        if wanted is None and seen is None:
            continue
        if wanted is None or seen is None or color_of(wanted) != color_of(seen):
            differences += 1
    return differences


def verify_trusted_observation(previous: Board, observed: list[str | None],
                               max_differences=2, max_plies=1):
    """Gate visual data through consistency and legal reverse validation.

    Returns ``(status, sequence, differences)``.  Vision only proposes a
    position: large deviations request a fresh frame; small deviations still
    have to be uniquely explainable by legal moves from the trusted board.
    """
    differences = occupancy_difference_count(previous.sq, observed)
    if differences > max_differences:
        return "retry", None, differences
    sequence = resolve_legal_sequence_by_occupancy(
        previous, observed, max_plies=max_plies)
    if sequence is None:
        return "illegal", None, differences
    if not sequence:
        return "unchanged", [], differences
    return "recovered", sequence, differences


def after_move(squares: list[str | None], move: tuple[int, int]) -> list[str | None]:
    result = squares[:]
    frm, to = move
    result[to], result[frm] = result[frm], None
    return result


def matches_after_move(previous: list[str | None], move: tuple[int, int],
                       observed: list[str | None], tolerant_indices=()) -> bool:
    """Match a transition while tolerating one highlighted-piece misclassification.

    JJ highlights the destination after a move.  That can change the glyph score,
    but occupancy, color, source clearing and every unaffected square must still
    agree exactly.
    """
    if len(previous) != 90 or len(observed) != 90:
        return False
    frm, to = move
    moving = previous[frm]
    if moving is None or observed[frm] is not None or observed[to] is None:
        return False
    if color_of(observed[to]) != color_of(moving):
        return False
    expected = after_move(previous, move)
    return trusted_position_matches(expected, observed,
                                    set(tolerant_indices) | {to})


def matches_move_with_limited_vision_loss(previous: list[str | None],
                                          move: tuple[int, int],
                                          observed: list[str | None],
                                          max_collateral=1) -> bool:
    """Confirm a sent move despite one unrelated false-negative square.

    JJ highlights can temporarily hide or restyle a different piece.  This
    fallback still requires the source to be empty and the destination to hold
    the mover's colour.  Only a tiny number of unrelated occupancy/side
    mismatches is allowed; callers must require consecutive stable frames.
    """
    if len(previous) != 90 or len(observed) != 90:
        return False
    frm, to = move
    moving = previous[frm]
    if (moving is None or observed[frm] is not None or observed[to] is None or
            color_of(observed[to]) != color_of(moving)):
        return False
    expected = after_move(previous, move)
    collateral = 0
    for index, (wanted, seen) in enumerate(zip(expected, observed)):
        if index in (frm, to):
            continue
        if wanted is None and seen is None:
            continue
        if (wanted is None or seen is None or
                color_of(wanted) != color_of(seen)):
            collateral += 1
            if collateral > max_collateral:
                return False
    return True


def resolve_one_legal_move(previous: Board, observed: list[str | None],
                           tolerant_indices=()):
    """Resolve an exact observed position to one unique legal move.

    Returning None is deliberate: an animation, highlight or classifier error must
    never be interpreted as an opponent move.
    """
    if len(observed) != 90 or same_position(previous.sq, observed):
        return None
    matches = []
    for move in previous.legal_moves():
        if matches_after_move(previous.sq, move, observed, tolerant_indices):
            matches.append(move)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def resolve_fast_reply(previous: Board, our_move: tuple[int, int],
                       observed: list[str | None], tolerant_indices=()):
    """Resolve a reply that arrived before our post-move screenshot.

    The trusted board is advanced by our already-sent legal move, then the
    observed board must still identify exactly one legal opponent response.
    """
    if our_move not in previous.legal_moves():
        return None
    after_ours = previous.copy()
    after_ours.apply(our_move)
    return resolve_one_legal_move(
        after_ours, observed, set(tolerant_indices) | {our_move[1]})


def resolve_legal_sequence_by_occupancy(previous: Board,
                                        observed: list[str | None],
                                        max_plies=2):
    """Uniquely recover 0..max_plies moves using occupancy and side only."""
    tolerant = range(90)
    if trusted_position_matches(previous.sq, observed, tolerant):
        return []
    frontier = [(previous.copy(), [])]
    for _depth in range(1, max_plies + 1):
        next_frontier = []
        matches = []
        for board, sequence in frontier:
            for move in board.legal_moves():
                child = board.copy()
                child.apply(move)
                candidate = sequence + [move]
                if trusted_position_matches(child.sq, observed, tolerant):
                    matches.append(candidate)
                    if len(matches) > 1:
                        return None
                next_frontier.append((child, candidate))
        if len(matches) == 1:
            return matches[0]
        frontier = next_frontier
    return None


def validate_board(board: Board) -> list[str]:
    errors = position_errors(board.sq)
    if errors:
        return errors
    # A legal position cannot leave the side that just moved in check.
    just_moved = BLACK if board.turn == RED else RED
    if board.in_check(just_moved):
        errors.append("side that just moved is in check")
    return errors
