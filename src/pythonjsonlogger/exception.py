### IMPORTS
### ============================================================================
## Future
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


class MaxBytesExceededError(PythonJsonLoggerError):
    "A log record could not be trimmed to fit within the configured `max_bytes`"

    def __init__(self, max_bytes: int, required_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.required_bytes = required_bytes
        super().__init__(
            f"Log record could not be trimmed to fit within max_bytes={max_bytes}: "
            f"the minimal record (required fields plus the trimming report) requires "
            f"{required_bytes} bytes. Increase max_bytes or remove fields from "
            "max_bytes_required_fields."
        )
        return
