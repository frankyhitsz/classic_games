"""Compact, deterministic Sokoban walk/push history without pygame."""

from __future__ import annotations

DIRECTIONS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
MAX_COMMANDS = 10_000


def encode_history(history, final_position) -> str:
    points = [item[0] for item in history] + [final_position]
    reverse = {direction: key for key, direction in DIRECTIONS.items()}
    if len(history) > MAX_COMMANDS:
        raise ValueError("too many undo steps")
    return "".join(reverse[(b[0] - a[0], b[1] - a[1])]
                   for a, b in zip(points, points[1:]))


def decode_history(commands, player, boxes, floors):
    if not isinstance(commands, str) or len(commands) > MAX_COMMANDS:
        raise ValueError("invalid Sokoban command history")
    boxes = set(boxes)
    history = []
    pushes = 0
    for moves, command in enumerate(commands):
        if command not in DIRECTIONS:
            raise ValueError("unknown Sokoban command")
        dx, dy = DIRECTIONS[command]
        destination = player[0] + dx, player[1] + dy
        if destination not in floors:
            raise ValueError("walk crosses a wall")
        history.append((player, set(boxes), moves, pushes))
        if destination in boxes:
            target = destination[0] + dx, destination[1] + dy
            if target not in floors or target in boxes:
                raise ValueError("illegal push")
            boxes.remove(destination)
            boxes.add(target)
            pushes += 1
        player = destination
    return history, player, boxes, pushes
