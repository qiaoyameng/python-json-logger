"""Explicit type adapter registry for converting objects to JSON compatible values.

Instead of a single `json_default` function full of `isinstance` checks, types can
be registered with an adapter callable. Formatters that are given a registry will
convert matching objects before serialization.

Adapters receive the object and its current [location][pythonjsonlogger.adapters.AdapterLocation]
so values and mapping keys can be handled with different rules. Conversion results
are processed recursively, so adapters may return containers or other registered
custom objects. Adapters must not return the object they were given or form
conversion cycles; doing so raises [AdapterCycleError][pythonjsonlogger.exception.AdapterCycleError].
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import enum
from typing import Any, TypeAlias
from collections.abc import Mapping
from collections.abc import Callable

## Installed

## Application
from .exception import AdapterCycleError, AmbiguousAdapterError


### TYPE ALIASES
### ============================================================================
TypeAdapter: TypeAlias = "Callable[[Any, AdapterLocation], Any]"
"""Callable that converts an object into a JSON compatible value.

Adapters receive the object to convert and its
[location][pythonjsonlogger.adapters.AdapterLocation], and must return a JSON
compatible value. Returning containers or other registered custom objects is
allowed; they are converted recursively.
"""


### CLASSES
### ============================================================================
class AdapterLocation(str, enum.Enum):
    """Where in the log data an object being converted was found."""

    VALUE = "value"
    """The object is a plain value (field value, container item)."""

    KEY = "key"
    """The object is a mapping key."""


class TypeAdapterRegistry:
    """Registry mapping types to adapter callables.

    Dispatch first checks for an exact type match, then selects the most
    specific adapter amongst registered base classes. If multiple registered
    base classes are incomparable (neither is a subclass of the other, e.g.
    with multiple inheritance) an
    [AmbiguousAdapterError][pythonjsonlogger.exception.AmbiguousAdapterError]
    is raised rather than relying on registration order.

    Registries can be updated from any thread. Call
    [snapshot()][pythonjsonlogger.adapters.TypeAdapterRegistry.snapshot] to get
    a stable view; formatters snapshot the registry once per `format` call so a
    single log record never sees a mix of rules.
    """

    def __init__(self, adapters: Mapping[type, TypeAdapter] | None = None) -> None:
        """
        Args:
            adapters: initial mapping of types to adapter callables
        """
        self._adapters: dict[type, TypeAdapter] = {}
        if adapters is not None:
            for type_, adapter in adapters.items():
                self.register(type_, adapter)
        return

    def __contains__(self, type_: type) -> bool:
        return type_ in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)

    def register(self, type_: type, adapter: TypeAdapter) -> None:
        """Register `adapter` for `type_`, replacing any existing adapter.

        Args:
            type_: the type the adapter handles
            adapter: callable receiving `(obj, location)` and returning a JSON
                compatible value
        """
        if not isinstance(type_, type):
            raise TypeError(f"Can only register adapters for types, got {type_!r}")
        if not callable(adapter):
            raise TypeError(f"Adapter must be callable, got {adapter!r}")
        self._adapters[type_] = adapter
        return

    def unregister(self, type_: type) -> None:
        """Remove the adapter registered for `type_` if any."""
        self._adapters.pop(type_, None)
        return

    def snapshot(self) -> TypeAdapterRegistry:
        """Return a stable copy of the current registrations.

        Updates to the original registry do not affect the snapshot and vice
        versa.
        """
        snapshot = TypeAdapterRegistry()
        snapshot._adapters = self._adapters.copy()  # pylint: disable=protected-access
        return snapshot

    def get_adapter(self, cls: type) -> TypeAdapter | None:
        """Find the adapter for `cls`.

        Args:
            cls: type to find an adapter for

        Returns:
            The adapter registered for the exact type or the most specific
            registered base class, or `None` if no adapter applies.

        Raises:
            AmbiguousAdapterError: multiple registered base classes apply and
                none is more specific than the others.
        """
        adapter = self._adapters.get(cls)
        if adapter is not None:
            return adapter

        candidates = [type_ for type_ in self._adapters if issubclass(cls, type_)]
        if not candidates:
            return None

        most_specific = [
            type_
            for type_ in candidates
            if not any(other is not type_ and issubclass(other, type_) for other in candidates)
        ]
        if len(most_specific) > 1:
            raise AmbiguousAdapterError(cls, most_specific)
        return self._adapters[most_specific[0]]


### FUNCTIONS
### ============================================================================
_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def convert_with_adapters(
    obj: Any,
    registry: TypeAdapterRegistry,
    location: AdapterLocation = AdapterLocation.VALUE,
    _active: set[int] | None = None,
) -> Any:
    """Recursively convert `obj` into JSON compatible values using `registry`.

    JSON primitives pass through unchanged. Mappings and `list`/`tuple`/`set`/
    `frozenset` containers are rebuilt with their contents converted; mapping
    keys are converted with [AdapterLocation.KEY][pythonjsonlogger.adapters.AdapterLocation].
    Other objects are converted using the registered adapter for their type, if
    any, otherwise they are returned unchanged (e.g. to be handled by a
    `json_default` fallback). Adapter results are converted recursively.

    Args:
        obj: object to convert
        registry: registry to source adapters from
        location: where `obj` was found, passed on to adapters

    Returns:
        The converted value. Containers are always new objects; the input is
        never mutated.

    Raises:
        AdapterCycleError: an adapter returned the object it was given, or a
            conversion cycle was detected.
        AmbiguousAdapterError: no single most specific adapter exists for a
            type.
    """
    if _active is None:
        _active = set()

    if isinstance(obj, _JSON_PRIMITIVES):
        return obj

    if isinstance(obj, dict):
        _check_cycle(obj, _active)
        _active.add(id(obj))
        try:
            return {
                convert_with_adapters(key, registry, AdapterLocation.KEY, _active): (
                    convert_with_adapters(value, registry, AdapterLocation.VALUE, _active)
                )
                for key, value in obj.items()
            }
        finally:
            _active.discard(id(obj))

    if isinstance(obj, (list, tuple, set, frozenset)):
        _check_cycle(obj, _active)
        _active.add(id(obj))
        try:
            items = [
                convert_with_adapters(item, registry, AdapterLocation.VALUE, _active)
                for item in obj
            ]
        finally:
            _active.discard(id(obj))
        if isinstance(obj, list):
            return items
        if isinstance(obj, tuple):
            return tuple(items)
        if isinstance(obj, set):
            return set(items)
        return frozenset(items)

    adapter = registry.get_adapter(type(obj))
    if adapter is None:
        return obj

    _check_cycle(obj, _active)
    _active.add(id(obj))
    try:
        result = adapter(obj, location)
        if result is obj:
            raise AdapterCycleError(
                f"Adapter for {type(obj).__qualname__!r} returned the object it was given"
            )
        return convert_with_adapters(result, registry, location, _active)
    finally:
        _active.discard(id(obj))


def _check_cycle(obj: Any, active: set[int]) -> None:
    if id(obj) in active:
        raise AdapterCycleError(
            f"Conversion cycle detected for {type(obj).__qualname__!r} object"
        )
    return
