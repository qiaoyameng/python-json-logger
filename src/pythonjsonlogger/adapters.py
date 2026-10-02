"""Explicit type adapters for converting log values before JSON serialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import threading
from typing import Any, TypeAlias


class AdapterLocation(Enum):
    """The location in a log structure where an object is being converted."""

    VALUE = "value"
    KEY = "key"


@dataclass(frozen=True)
class AdapterPosition:
    """Information about where an adapter is being called."""

    location: AdapterLocation
    path: tuple[Any, ...] = ()


TypeAdapter: TypeAlias = Callable[[Any, AdapterPosition], Any]


class TypeAdapterRegistrySnapshot:
    """An immutable snapshot of a `TypeAdapterRegistry`."""

    def __init__(self, adapters: dict[type[Any], TypeAdapter]) -> None:
        self._adapters = adapters.copy()

    def get_adapter(
        self, obj: Any, location: AdapterLocation = AdapterLocation.VALUE
    ) -> tuple[TypeAdapter | None, type[Any] | None]:
        """Return the most specific adapter for `obj`.

        Exact type matches take precedence over inherited matches. Among inherited
        matches, adapters registered for base types are removed when an adapter for
        a more specific registered type exists. Multiple unrelated minimal matches
        are ambiguous.
        """
        obj_type = type(obj)
        exact_adapter = self._adapters.get(obj_type)
        if exact_adapter is not None:
            return exact_adapter, obj_type

        candidates = self._candidate_types(obj, self._adapters)
        selected_type = self._select_type(obj_type, candidates)
        if selected_type is None:
            return None, None
        return self._adapters[selected_type], selected_type

    @staticmethod
    def _candidate_types(
        obj: Any, adapters: dict[type[Any], TypeAdapter]
    ) -> list[type[Any]]:
        return [
            registered_type for registered_type in adapters if isinstance(obj, registered_type)
        ]

    @staticmethod
    def _select_type(
        obj_type: type[Any], candidates: list[type[Any]]
    ) -> type[Any] | None:
        minimal_candidates = [
            candidate
            for candidate in candidates
            if not any(
                other is not candidate and issubclass(other, candidate)
                for other in candidates
            )
        ]

        if not minimal_candidates:
            return None

        if len(minimal_candidates) > 1:
            names = sorted(
                f"{candidate.__module__}.{candidate.__qualname__}"
                for candidate in minimal_candidates
            )
            raise TypeError(
                f"Ambiguous type adapters for {obj_type.__module__}.{obj_type.__qualname__}: "
                + ", ".join(names)
            )

        return minimal_candidates[0]

    def convert(
        self,
        obj: Any,
        location: AdapterLocation = AdapterLocation.VALUE,
        path: tuple[Any, ...] = (),
    ) -> Any:
        """Convert `obj` and any custom objects in its converted result."""
        active: dict[int, Any] = {}

        def convert_value(
            value: Any,
            current_location: AdapterLocation,
            current_path: tuple[Any, ...],
            adapter_chain: tuple[tuple[int, type[Any]], ...] = (),
        ) -> Any:
            adapter, _ = self.get_adapter(value, current_location)
            if adapter is None:
                return convert_container(value, current_location, current_path, ())

            value_id = id(value)
            if value_id in active:
                raise TypeError(f"Circular reference detected at {self._format_path(current_path)}")
            active[value_id] = value
            try:
                converted = adapter(value, AdapterPosition(current_location, current_path))
                if converted is value:
                    location_text = self._format_path(current_path)
                    raise TypeError(
                        f"Type adapter returned the original object at {location_text}"
                    )
                converted_adapter, _ = self.get_adapter(converted, current_location)
                if converted_adapter is None:
                    return convert_container(converted, current_location, current_path, ())

                next_adapter_node = (id(converted_adapter), type(converted))
                if next_adapter_node in adapter_chain:
                    raise TypeError(
                        f"Circular reference detected at {self._format_path(current_path)}"
                    )
                return convert_value(
                    converted,
                    current_location,
                    current_path,
                    adapter_chain + ((id(adapter), type(value)),),
                )
            finally:
                active.pop(value_id, None)

        def convert_container(
            value: Any,
            current_location: AdapterLocation,
            current_path: tuple[Any, ...],
            adapter_chain: tuple[tuple[int, type[Any]], ...],
        ) -> Any:
            if isinstance(value, dict):
                value_id = id(value)
                if value_id in active:
                    raise TypeError(
                        f"Circular reference detected at {self._format_path(current_path)}"
                    )
                active[value_id] = value
                try:
                    converted: dict[Any, Any] = {}
                    for key, item in value.items():
                        item_path = current_path + (key,)
                        converted_key = convert_value(key, AdapterLocation.KEY, item_path)
                        converted[converted_key] = convert_value(
                            item, AdapterLocation.VALUE, item_path, adapter_chain
                        )
                    return converted
                finally:
                    active.pop(value_id, None)

            if isinstance(value, (list, tuple)):
                value_id = id(value)
                if value_id in active:
                    raise TypeError(
                        f"Circular reference detected at {self._format_path(current_path)}"
                    )
                active[value_id] = value
                try:
                    items = [
                        convert_value(
                            item,
                            current_location,
                            current_path + (index,),
                            adapter_chain,
                        )
                        for index, item in enumerate(value)
                    ]
                    return tuple(items) if isinstance(value, tuple) else items
                finally:
                    active.pop(value_id, None)

            return value

        return convert_value(obj, location, path)

    @staticmethod
    def _format_path(path: tuple[Any, ...]) -> str:
        if not path:
            return "$"
        return "$" + "".join(
            f"[{item!r}]" if isinstance(item, str) else f"[{item}]" for item in path
        )


class TypeAdapterRegistry:
    """A thread-safe registry of explicit type adapters."""

    def __init__(self) -> None:
        self._adapters: dict[type[Any], TypeAdapter] = {}
        self._lock = threading.RLock()

    def register(self, type_: type[Any], adapter: TypeAdapter) -> None:
        """Register an adapter for an exact type and its subclasses."""
        if not isinstance(type_, type):
            raise TypeError("type_ must be a class")
        if not callable(adapter):
            raise TypeError("adapter must be callable")

        with self._lock:
            self._adapters[type_] = adapter

    def unregister(self, type_: type[Any]) -> None:
        """Remove a registered adapter."""
        with self._lock:
            del self._adapters[type_]

    def snapshot(self) -> TypeAdapterRegistrySnapshot:
        """Return a stable snapshot for use during one conversion."""
        with self._lock:
            return TypeAdapterRegistrySnapshot(self._adapters)
