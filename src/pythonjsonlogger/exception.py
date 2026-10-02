### IMPORTS
### ============================================================================
## Future
from __future__ import annotations
from __future__ import annotations

## Standard Library

## Installed

## Application


### CLASSES
### ============================================================================
class PythonJsonLoggerError(Exception):
    "Generic base clas for all Python JSON Logger exceptions"


class MissingPackageError(ImportError, PythonJsonLoggerError):
    "A required package is missing"

    def __init__(self, name: str, extras_name: str | None = None) -> None:
        msg = f"The {name!r} package is required but could not be found."
        if extras_name is not None:
            msg += f" It can be installed using 'python-json-logger[{extras_name}]'."
        super().__init__(msg)
        return


class AdapterError(PythonJsonLoggerError):
    "Base class for type adapter registry errors"


class AmbiguousAdapterError(AdapterError):
    "No single most specific adapter exists for a type"

    def __init__(self, cls: type, candidates: list[type]) -> None:
        self.cls = cls
        self.candidates = tuple(candidates)
        names = ", ".join(candidate.__qualname__ for candidate in candidates)
        super().__init__(
            f"Multiple adapters could apply to {cls.__qualname__!r} with no most"
            f" specific candidate: {names}"
        )
        return


class AdapterCycleError(AdapterError):
    "An adapter returned the object it was given or a conversion cycle was detected"
