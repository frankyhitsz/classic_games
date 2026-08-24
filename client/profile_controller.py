"""Generation guards for asynchronous launcher profile operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProfileOperation:
    kind: str
    future: object
    generation: int
    expected_profile_id: Optional[str]


@dataclass(frozen=True)
class ProfileLaunchToken:
    game_id: str
    generation: int
    expected_profile_id: Optional[str]


class ProfileController:
    """Owns tokens that prevent late Futures from changing a new choice."""

    def __init__(self, profile_id: str):
        self.generation = 0
        self.profile_id = profile_id
        self.identity_resolved = False
        self._operations: dict[str, ProfileOperation] = {}
        self._queued_launch: Optional[ProfileLaunchToken] = None

    def select(self, profile_id: str) -> int:
        self.generation += 1
        self.profile_id = profile_id
        self.identity_resolved = True
        self._queued_launch = None
        return self.generation

    def resolve(self, profile_id: str) -> None:
        """Bind the startup placeholder after last-profile has completed."""
        self.profile_id = profile_id
        self.identity_resolved = True

    def bind(self, kind: str, future, *,
             expected_profile_id: Optional[str] = None,
             match_profile: bool = True) -> ProfileOperation:
        operation = ProfileOperation(
            kind=kind, future=future, generation=self.generation,
            expected_profile_id=(
                self.profile_id if match_profile and expected_profile_id is None
                else expected_profile_id),
        )
        self._operations[kind] = operation
        return operation

    def completed(self, kind: str) -> Optional[ProfileOperation]:
        operation = self._operations.get(kind)
        if operation is None or not operation.future.done():
            return None
        self._operations.pop(kind, None)
        return operation

    def is_current(self, operation: ProfileOperation) -> bool:
        return (operation.generation == self.generation
                and (operation.expected_profile_id is None
                     or operation.expected_profile_id == self.profile_id))

    def has_operation(self, kind: str) -> bool:
        operation = self._operations.get(kind)
        return operation is not None and self.is_current(operation)

    def queue_launch(self, game_id: str) -> ProfileLaunchToken:
        token = ProfileLaunchToken(
            game_id, self.generation,
            self.profile_id if self.identity_resolved else None)
        self._queued_launch = token
        return token

    def pop_ready_launch(self, *, ready: bool) -> Optional[str]:
        token = self._queued_launch
        if token is None or not ready:
            return None
        self._queued_launch = None
        if (token.generation != self.generation
                or (token.expected_profile_id is not None
                    and token.expected_profile_id != self.profile_id)):
            return None
        return token.game_id
