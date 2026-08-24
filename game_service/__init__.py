"""Shared game metadata and local persistence services."""

from .catalog import GAMES, VALID_GAME_IDS
from .service import DataResult, GameDataService, StorageStatus
from .store import LocalGameStore, StoreError, default_database_path

__all__ = [
    "GAMES",
    "DataResult",
    "GameDataService",
    "StorageStatus",
    "VALID_GAME_IDS",
    "LocalGameStore",
    "StoreError",
    "default_database_path",
]
