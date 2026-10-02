"""JSON formatter using the standard library's `json` for encoding.

Module contains the `JsonFormatter` and a custom `JsonEncoder` which supports a greater
variety of types.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import datetime
import json
from typing import Any
from collections.abc import Callable, Container, Sequence
import warnings

## Application
from . import core
from . import defaults as d


### CLASSES
### ============================================================================
class JsonEncoder(json.JSONEncoder):
    """A custom encoder extending [json.JSONEncoder](https://docs.python.org/3/library/json.html#json.JSONEncoder)"""

    def default(self, o: Any) -> Any:
        if d.use_datetime_any(o):
            return self.format_datetime_obj(o)

        if d.use_exception_default(o):
            return d.exception_default(o)

        if d.use_traceback_default(o):
            return d.traceback_default(o)

        if d.use_enum_default(o):
            return d.enum_default(o)

        if d.use_bytes_default(o):
            return d.bytes_default(o)

        if d.use_dataclass_default(o):
            return d.dataclass_default(o)

        if d.use_type_default(o):
            return d.type_default(o)

        try:
            return super().default(o)
        except TypeError:
            return d.unknown_default(o)

    def format_datetime_obj(self, o: datetime.time | datetime.date | datetime.datetime) -> str:
        """Format datetime objects found in `self.default`

        This allows subclasses to change the datetime format without understanding the
        internals of the default method.
        """
        return d.datetime_any(o)


class JsonFormatter(core.BaseJsonFormatter):
    """JSON formatter using the standard library's [`json`](https://docs.python.org/3/library/json.html) for encoding"""

    def __init__(
        self,
        *args,
        json_default: Callable | None = None,
        json_encoder: Callable | None = None,
        json_serializer: Callable = json.dumps,
        json_indent: int | str | None = None,
        json_ensure_ascii: bool = True,
        max_bytes: int | None = None,
        required_fields: Container[str] | None = None,
        field_priority: Sequence[str] | None = None,
        **kwargs,
    ) -> None:
        """
        Args:
            args: see [BaseJsonFormatter][pythonjsonlogger.core.BaseJsonFormatter]
            json_default: a function for encoding non-standard objects
            json_encoder: custom JSON encoder
            json_serializer: a [`json.dumps`](https://docs.python.org/3/library/json.html#json.dumps)-compatible callable
                that will be used to serialize the log record.
            json_indent: indent parameter for the `json_serializer`
            json_ensure_ascii: `ensure_ascii` parameter for the `json_serializer`
            max_bytes: maximum allowed UTF-8 byte length of a serialized log
                record (including `prefix`). Records that exceed the budget are
                trimmed; see
                [trim_log_data_to_budget][pythonjsonlogger.core.trim_log_data_to_budget].
                `None` (the default) disables trimming.
            required_fields: field names (after renaming) that are never trimmed
                or removed when enforcing `max_bytes`, e.g. `["asctime", "levelname"]`.
            field_priority: field names (after renaming) to sacrifice first when
                enforcing `max_bytes`, in order. Remaining non-required fields
                are trimmed afterwards, most recently added first.
            kwargs: see [BaseJsonFormatter][pythonjsonlogger.core.BaseJsonFormatter]

        *Added in 4.2.1*: `max_bytes`, `required_fields`, and `field_priority`.

        Raises:
            ValueError: if `max_bytes` is not a positive integer.
        """
        super().__init__(*args, **kwargs)

        self.json_default = json_default
        self.json_encoder = json_encoder
        self.json_serializer = json_serializer
        self.json_indent = json_indent
        self.json_ensure_ascii = json_ensure_ascii
        if not self.json_encoder and not self.json_default:
            self.json_encoder = JsonEncoder

        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer or None")
        self.max_bytes = max_bytes
        self.required_fields = set(required_fields) if required_fields is not None else set()
        self.field_priority = list(field_priority) if field_priority is not None else []
        return

    def jsonify_log_record(self, log_data: core.LogData) -> str:
        """Returns a json string of the log data."""
        return self.json_serializer(
            log_data,
            default=self.json_default,
            cls=self.json_encoder,
            indent=self.json_indent,
            ensure_ascii=self.json_ensure_ascii,
        )

    def serialize_log_record(self, log_data: core.LogData) -> str:
        """Returns the final representation of the data to be logged

        When `max_bytes` is set and the serialized record exceeds the UTF-8 byte
        budget, the record is trimmed with
        [trim_log_data_to_budget][pythonjsonlogger.core.trim_log_data_to_budget]
        before serialization. Records that already fit (including exactly equal
        to the budget) are returned unchanged.

        Raises:
            pythonjsonlogger.exception.LogRecordTooLargeError: when the record
                cannot be trimmed to fit within `max_bytes`.
        """
        if self.max_bytes is None:
            return super().serialize_log_record(log_data)

        budget = self.max_bytes - len(self.prefix.encode("utf-8"))
        serialized = self.jsonify_log_record(log_data)
        if len(serialized.encode("utf-8")) <= budget:
            return self.prefix + serialized

        trimmed = core.trim_log_data_to_budget(
            log_data,
            serialize=self.jsonify_log_record,
            max_bytes=budget,
            required_fields=self.required_fields,
            field_priority=self.field_priority,
        )
        return self.prefix + self.jsonify_log_record(trimmed)


### DEPRECATED COMPATIBILITY
### ============================================================================
def __getattr__(name: str):
    if name == "RESERVED_ATTRS":
        warnings.warn(
            "RESERVED_ATTRS has been moved to pythonjsonlogger.core",
            DeprecationWarning,
        )
        return core.RESERVED_ATTRS
    raise AttributeError(f"module {__name__} has no attribute {name}")
