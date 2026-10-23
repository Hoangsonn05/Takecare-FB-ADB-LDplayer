from __future__ import annotations

import threading

from .models import DeviceAccountBinding


class AccountBindingError(ValueError):
    pass


class AccountBindingRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_ld: dict[int, DeviceAccountBinding] = {}
        self._active_uids: set[str] = set()

    def bind(self, binding: DeviceAccountBinding) -> None:
        uid = str(binding.facebook_uid).strip()
        if not uid:
            raise AccountBindingError("Facebook UID is required")
        with self._lock:
            for ld_index, existing in self._by_ld.items():
                if ld_index != binding.ld_index and existing.facebook_uid == uid:
                    raise AccountBindingError(
                        f"Facebook UID is already bound to LD-{ld_index}"
                    )
            if uid in self._active_uids:
                raise AccountBindingError("Facebook UID is currently used by a worker")
            self._by_ld[int(binding.ld_index)] = binding

    def remove(self, ld_index: int) -> None:
        with self._lock:
            binding = self._by_ld.get(int(ld_index))
            if binding and binding.facebook_uid in self._active_uids:
                raise AccountBindingError("Cannot remove an active binding")
            self._by_ld.pop(int(ld_index), None)

    def clear(self) -> None:
        with self._lock:
            if self._active_uids:
                raise AccountBindingError("Cannot clear bindings while workers are active")
            self._by_ld.clear()

    def bindings(self) -> list[DeviceAccountBinding]:
        with self._lock:
            return [self._by_ld[index] for index in sorted(self._by_ld)]

    def mark_active(self, binding: DeviceAccountBinding) -> None:
        with self._lock:
            if binding.facebook_uid in self._active_uids:
                raise AccountBindingError("Facebook UID is already active")
            self._active_uids.add(binding.facebook_uid)

    def mark_inactive(self, binding: DeviceAccountBinding) -> None:
        with self._lock:
            self._active_uids.discard(binding.facebook_uid)
