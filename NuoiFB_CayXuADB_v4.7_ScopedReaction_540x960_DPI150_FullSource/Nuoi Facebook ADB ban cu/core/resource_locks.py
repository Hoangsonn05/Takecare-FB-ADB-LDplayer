from __future__ import annotations

import threading


class ResourceLockRegistry:
    """Process-local ownership registry shared by nurture and Auto Job workers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ld_owners: dict[int, str] = {}

    def claim_ld(self, index: int, owner: str) -> bool:
        index = int(index)
        owner = str(owner)
        with self._lock:
            current = self._ld_owners.get(index)
            if current is not None and current != owner:
                return False
            self._ld_owners[index] = owner
            return True

    def release_ld(self, index: int, owner: str) -> None:
        index = int(index)
        with self._lock:
            if self._ld_owners.get(index) == str(owner):
                self._ld_owners.pop(index, None)

    def owner_for_ld(self, index: int) -> str | None:
        with self._lock:
            return self._ld_owners.get(int(index))

    def active_ld_indices(self) -> list[int]:
        with self._lock:
            return sorted(self._ld_owners)


GLOBAL_RESOURCE_LOCKS = ResourceLockRegistry()
