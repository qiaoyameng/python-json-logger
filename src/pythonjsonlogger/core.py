"""Core functionality shared by all JSON loggers"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from datetime import datetime, timezone
import logging
import re
import string
import sys
from typing import TypeAlias, Any
from collections.abc import Callable, Container, Sequence

## Installed

## Application
from .exception import MaxBytesExceededError


### CONSTANTS
### ============================================================================
RESERVED_ATTRS: list[str] = [
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
]
"""Default reserved attributes.

These come from the [default attributes of `LogRecord` objects](http://docs.python.org/library/logging.html#logrecord-attributes).

Note:
    Although considered a constant, this list is dependent on the Python version due to
    different `LogRecord` objects having different attributes in different Python versions.

*Changed in 3.0*: `RESERVED_ATTRS` is now `list[str]` instead of `tuple[str, ...]`.
"""

if sys.version_info >= (3, 12):
    # taskName added in python 3.12
    RESERVED_ATTRS.append("taskName")
    RESERVED_ATTRS.sort()


STYLE_STRING_TEMPLATE_REGEX = re.compile(
    r"\$(?:\$|\{(?P<braced>.+?)\}|(?P<named>[_a-z][_a-z0-9]*))", re.IGNORECASE
)  # $ style
STYLE_PERCENT_REGEX = re.compile(r"%(?:%|\((?P<named>.+?)\))", re.IGNORECASE)  # % style

## Type Aliases
## -----------------------------------------------------------------------------
LogData: TypeAlias = dict[str, Any]
"""Type alias

*Changed in 4.0*: renamed from `LogRecord` to `LogData`
"""

DEFAULT_MAX_BYTES_REQUIRED_FIELDS: tuple[str, ...] = (
    "asctime",
    "levelname",
    "levelno",
    "timestamp",
)
"""Default fields that are never truncated or dropped when enforcing `max_bytes`.

*Added in 4.3*
"""

TRUNCATED_VALUE_MARKER = "...[truncated]"
"""Marker appended to truncated string values and used to mark truncated arrays.

*Added in 4.3*
"""

TRUNCATED_DICT_MARKER_KEY = "__truncated__"
"""Key added to truncated objects, holding the number of removed keys.

*Added in 4.3*
"""


### FUNCTIONS
### ============================================================================
def merge_record_extra(
    record: logging.LogRecord,
    target: dict[Any, Any],
    reserved: Container[str],
    rename_fields: dict[str, str] | None = None,
) -> dict[Any, Any]:
    """
    Merges extra attributes from LogRecord object into target dictionary

    Args:
        record: logging.LogRecord
        target: dict to update
        reserved: dict or list with reserved keys to skip
        rename_fields: an optional dict, used to rename field names in the output.
            e.g. Rename `levelname` to `log.level`: `{'levelname': 'log.level'}`

    *Changed in 3.1*: `reserved` is now `Container[str]`.
    """
    if rename_fields is None:
        rename_fields = {}
    for key, value in record.__dict__.items():
        # this allows to have numeric keys
        if key not in reserved and not (hasattr(key, "startswith") and key.startswith("_")):
            target[rename_fields.get(key, key)] = value
    return target


### CLASSES
### ============================================================================
class BaseJsonFormatter(logging.Formatter):
    """Base class for all formatters

    Must not be used directly.

    *New in 3.1*
    """

    _style: logging.PercentStyle | str  # type: ignore[assignment]

    ## Parent Methods
    ## -------------------------------------------------------------------------
    # pylint: disable=too-many-arguments,super-init-not-called
    def __init__(
        self,
        fmt: str | Sequence[str] | None = None,
        datefmt: str | None = None,
        style: str = "%",
        validate: bool = True,
        *,
        prefix: str = "",
        rename_fields: dict[str, str] | None = None,
        rename_fields_keep_missing: bool = False,
        static_fields: dict[str, Any] | None = None,
        reserved_attrs: Sequence[str] | None = None,
        timestamp: bool | str = False,
        defaults: dict[str, Any] | None = None,
        exc_info_as_array: bool = False,
        stack_info_as_array: bool = False,
        max_bytes: int | None = None,
        max_bytes_required_fields: Sequence[str] | None = None,
        max_bytes_field_priority: Sequence[str] | None = None,
        max_bytes_report_field: str = "_truncated",
    ) -> None:
        """
        Args:
            fmt: String format or `Sequence` of field names of fields to log.
            datefmt: format to use when formatting `asctime` field
            style: how to extract log fields from `fmt`. Ignored if `fmt` is a `Sequence[str]`.
            validate: validate `fmt` against style, if implementing a custom `style` you
                must set this to `False`. Ignored if `fmt` is a `Sequence[str]`.
            defaults: a dictionary containing default fields that are added before all other fields and
                may be overridden. The supplied fields are still subject to `rename_fields`.
            prefix: an optional string prefix added at the beginning of
                the formatted string
            rename_fields: an optional dict, used to rename field names in the output.
                Rename `message` to `@message`: `{'message': '@message'}`
            rename_fields_keep_missing: When renaming fields, include missing fields in the output.
            static_fields: an optional dict, used to add fields with static values to all logs
            reserved_attrs: an optional list of fields that will be skipped when
                outputting json log record. Defaults to [all log record attributes][pythonjsonlogger.core.RESERVED_ATTRS].
            timestamp: an optional string/boolean field to add a timestamp when
                outputting the json log record. If string is passed, timestamp will be added
                to log record using string as key. If True boolean is passed, timestamp key
                will be "timestamp". Defaults to False/off.
            exc_info_as_array: break the exc_info into a list of lines based on line breaks.
            stack_info_as_array: break the stack_info into a list of lines based on line breaks.
            max_bytes: maximum size of the serialized record in UTF-8 bytes, including
                `prefix`. Records that exceed the budget are trimmed (see notes below).
                `None` (default) disables the limit.
            max_bytes_required_fields: fields that are never truncated or dropped when
                enforcing `max_bytes`. Names refer to the final output fields (i.e. after
                `rename_fields` is applied). Defaults to [time and level fields]
                [pythonjsonlogger.core.DEFAULT_MAX_BYTES_REQUIRED_FIELDS].
            max_bytes_field_priority: field names ordered by trimming priority - the
                first field is truncated or dropped first. Fields not listed are trimmed
                afterwards, starting with the most recently added field.
            max_bytes_report_field: name of the field used to report which fields were
                truncated or dropped. The report itself counts towards `max_bytes`.

        *Changed in 3.1*:

        - you can now use custom values for style by setting validate to `False`.
          The value is stored in `self._style` as a string. The `parse` method will need to be
          overridden in order to support the new style.
        - Renaming fields now preserves the order that fields were added in and avoids adding
          missing fields. The original behaviour, missing fields have a value of `None`, is still
          available by setting `rename_fields_keep_missing` to `True`.

        *Changed in 3.2*: `defaults` argument is no longer ignored.

        *Added in 3.3*: `exc_info_as_array` and `stack_info_as_array` options are added.

        *Added in 4.0*:

        - `fmt` now supports comma separated lists (`style=","`). Note that this style is specific
          to `python-json-logger` and thus care should be taken to not to pass this format to other
          logging Formatter implementations.
        - `fmt` now supports sequences of strings (e.g. lists and tuples) of field names.

        *Added in 4.3*: `max_bytes` and related arguments. When the serialized record
        exceeds `max_bytes` UTF-8 bytes, non-required fields are first truncated (string
        values are shortened and arrays/objects are reduced, always leaving a
        truncation marker) and then dropped entirely, following
        `max_bytes_field_priority`. The final JSON string is never cut directly; the
        budget is always checked against the actual encoded output, so encoder settings
        such as `ensure_ascii`, escaping and separators are accounted for. If even the
        minimal record (required fields plus the trimming report) exceeds the budget,
        [MaxBytesExceededError][pythonjsonlogger.exception.MaxBytesExceededError] is
        raised instead of retrying indefinitely.
        """
        ## logging.Formatter compatibility
        ## ---------------------------------------------------------------------
        if fmt is None or isinstance(fmt, str):
            if style in logging._STYLES:
                _style = logging._STYLES[style][0](fmt)  # type: ignore[operator]
                if validate:
                    _style.validate()
                self._style = _style
                self._fmt = _style._fmt

            elif style == "," or not validate:
                self._style = style
                self._fmt = fmt
                # TODO: Validate comma format

            else:
                raise ValueError("Style must be one of: '%{$,'")

            self._required_fields = self.parse()

        # Note: we do this check second as string is still a Sequence[str]
        elif isinstance(fmt, Sequence):
            self._style = "__sequence__"
            self._fmt = str(fmt)
            self._required_fields = list(fmt)

        self.datefmt = datefmt

        ## JSON Logging specific
        ## ---------------------------------------------------------------------
        self.prefix = prefix

        # We recreate the dict in rename_fields and static_fields to support internal/external
        # references which require getting the item to do the conversion.
        # For more details see: https://github.com/nhairs/python-json-logger/pull/45
        self.rename_fields = (
            {key: rename_fields[key] for key in rename_fields} if rename_fields is not None else {}
        )
        self.static_fields = (
            {key: static_fields[key] for key in static_fields} if static_fields is not None else {}
        )

        self.rename_fields_keep_missing = rename_fields_keep_missing
        self.reserved_attrs = set(reserved_attrs if reserved_attrs is not None else RESERVED_ATTRS)
        self.timestamp = timestamp

        self._skip_fields = set(self._required_fields)
        self._skip_fields.update(self.reserved_attrs)
        self.defaults = defaults if defaults is not None else {}
        self.exc_info_as_array = exc_info_as_array
        self.stack_info_as_array = stack_info_as_array
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self.max_bytes_required_fields = tuple(
            max_bytes_required_fields
            if max_bytes_required_fields is not None
            else DEFAULT_MAX_BYTES_REQUIRED_FIELDS
        )
        self.max_bytes_field_priority = (
            tuple(max_bytes_field_priority) if max_bytes_field_priority is not None else ()
        )
        self.max_bytes_report_field = max_bytes_report_field
        return

    def format(self, record: logging.LogRecord) -> str:
        """Formats a log record and serializes to json

        When `record.msg` is a `dict`, adding `exc_info` and `stack_info` does not
        modify the caller's dict.

        Args:
            record: the record to format

        *Changed in 4.2.0*: a `dict` `record.msg` is copied instead of modified
        in place.
        """
        message_dict: dict[str, Any] = {}
        # TODO: logging.LogRecord.msg and logging.LogRecord.message in typeshed
        #        are always type of str. We shouldn't need to override that.
        if isinstance(record.msg, dict):
            message_dict = record.msg.copy()
            record.message = ""
        else:
            record.message = record.getMessage()

        # only format time if needed
        if "asctime" in self._required_fields:
            record.asctime = self.formatTime(record, self.datefmt)

        # Display formatted exception, but allow overriding it in the
        # user-supplied dict.
        if record.exc_info and not message_dict.get("exc_info"):
            message_dict["exc_info"] = self.formatException(record.exc_info)
        if not message_dict.get("exc_info") and record.exc_text:
            message_dict["exc_info"] = record.exc_text

        # Display formatted record of stack frames
        # default format is a string returned from :func:`traceback.print_stack`
        if record.stack_info and not message_dict.get("stack_info"):
            message_dict["stack_info"] = self.formatStack(record.stack_info)

        log_data: LogData = {}
        self.add_fields(log_data, record, message_dict)
        log_data = self.process_log_record(log_data)

        return self.serialize_log_record(log_data)

    ## JSON Formatter Specific Methods
    ## -------------------------------------------------------------------------
    def parse(self) -> list[str]:
        """Parses format string looking for substitutions

        This method is responsible for returning a list of fields (as strings)
        to include in all log messages.

        You can support custom styles by overriding this method.

        Returns:
            list of fields to be extracted and serialized
        """
        if self._fmt is None:
            return []

        if isinstance(self._style, str):
            if self._style == "__sequence__":
                raise RuntimeError("Must not call parse when fmt is a sequence of strings")

            if self._style == ",":
                return [field.strip() for field in self._fmt.split(",") if field.strip()]

            raise ValueError(f"Style {self._style!r} is not supported")

        if isinstance(self._style, logging.StringTemplateStyle):
            # String templates support both ${name} and $name, and $$ is an escaped literal
            return [
                match.group("braced") or match.group("named")
                for match in STYLE_STRING_TEMPLATE_REGEX.finditer(self._fmt)
                if match.group("braced") or match.group("named")
            ]

        if isinstance(self._style, logging.StrFormatStyle):
            return [
                field_name
                for _, field_name, _, _ in string.Formatter().parse(self._fmt)
                if field_name
            ]

        if isinstance(self._style, logging.PercentStyle):
            # PercentStyle is parent class of StringTemplateStyle and StrFormatStyle
            # so it must be checked last.
            return [
                match.group("named")
                for match in STYLE_PERCENT_REGEX.finditer(self._fmt)
                if match.group("named")
            ]

        raise ValueError(f"Style {self._style!r} is not supported")

    def serialize_log_record(self, log_data: LogData) -> str:
        """Returns the final representation of the data to be logged

        Args:
            log_data: the data

        *Changed in 4.0*: `log_record` renamed to `log_data`

        *Changed in 4.3*: when `max_bytes` is set, records exceeding the budget are
        trimmed (see `__init__` for details).
        """
        serialized = self.prefix + self.jsonify_log_record(log_data)
        if self.max_bytes is None or len(serialized.encode("utf-8")) <= self.max_bytes:
            return serialized
        return self._serialize_log_record_within_budget(log_data)

    def _max_bytes_trim_order(self, data: dict[str, Any], required: set[str]) -> list[str]:
        """Return the order in which fields of `data` are trimmed or dropped"""
        priority = [
            field
            for field in self.max_bytes_field_priority
            if field in data and field not in required
        ]
        remainder = [
            field
            for field in reversed(data)
            if field not in required and field not in priority
        ]
        return priority + remainder

    def _serialize_log_record_within_budget(self, log_data: LogData) -> str:
        """Trim log data so that the serialized record fits within `max_bytes`.

        The caller's `log_data` (and therefore the original `LogRecord`) is never
        mutated: trimmed values are always rebuilt instead of modified in place.

        The budget is always checked against the actual serialized output, so encoder
        settings (`ensure_ascii`, escaping, separators, indent, ...) are accounted for.
        The algorithm is guaranteed to terminate: it makes a single pass over the
        candidate fields to truncate them, then a single pass to drop them, and each
        binary search is bounded by the size of the value being trimmed.
        """
        assert self.max_bytes is not None  # noqa: S101 # guaranteed by caller
        max_bytes = self.max_bytes

        data = dict(log_data)
        required = set(self.max_bytes_required_fields)
        report: dict[str, list[str]] = {"truncated_fields": [], "dropped_fields": []}
        report_key = self.max_bytes_report_field
        while report_key in data:
            report_key = "_" + report_key

        def serialize(current: dict[str, Any]) -> str:
            if report["truncated_fields"] or report["dropped_fields"]:
                current = {**current, report_key: report}
            return self.prefix + self.jsonify_log_record(current)

        def attempt(current: dict[str, Any]) -> str | None:
            serialized = serialize(current)
            return serialized if len(serialized.encode("utf-8")) <= max_bytes else None

        def best_size(check: Callable[[int], bool], maximum: int) -> int:
            """Largest size in `[0, maximum)` for which `check(size)` fits.

            Assumes size `0` fits, `maximum` does not, and that the serialized size
            grows monotonically with `size`. `check` must apply the candidate value
            of the given size to `data` as a side effect.
            """
            low, high = 0, maximum
            while high - low > 1:
                mid = (low + high) // 2
                if check(mid):
                    low = mid
                else:
                    high = mid
            return low

        # Phase 1: truncate field values, always leaving a truncation marker.
        for field in self._max_bytes_trim_order(data, required):
            result = attempt(data)
            if result is not None:
                return result

            value = data[field]
            if isinstance(value, str) and value:
                data[field] = TRUNCATED_VALUE_MARKER
                report["truncated_fields"].append(field)
                if attempt(data) is None:
                    continue  # even the marker does not fit; try the next field

                def check_string(size: int, field: str = field, value: str = value) -> bool:
                    data[field] = value[:size] + TRUNCATED_VALUE_MARKER
                    return attempt(data) is not None

                size = best_size(check_string, len(value))
                data[field] = value[:size] + TRUNCATED_VALUE_MARKER
                return serialize(data)

            if isinstance(value, (list, tuple)) and value:
                items = list(value)

                def make_list(size: int, items: list = items) -> list:
                    return items[:size] + [f"...[{len(items) - size} item(s) truncated]"]

                data[field] = make_list(0)
                report["truncated_fields"].append(field)
                if attempt(data) is None:
                    continue

                def check_list(
                    size: int, field: str = field, make_list: Callable = make_list
                ) -> bool:
                    data[field] = make_list(size)
                    return attempt(data) is not None

                size = best_size(check_list, len(items))
                data[field] = make_list(size)
                return serialize(data)

            if isinstance(value, dict) and value:
                items = list(value.items())
                marker_key = TRUNCATED_DICT_MARKER_KEY
                existing = {key for key, _ in items}
                while marker_key in existing:
                    marker_key = "_" + marker_key

                def make_dict(size: int, items: list = items, marker_key: str = marker_key) -> dict:
                    trimmed = dict(items[:size])
                    trimmed[marker_key] = len(items) - size
                    return trimmed

                data[field] = make_dict(0)
                report["truncated_fields"].append(field)
                if attempt(data) is None:
                    continue

                def check_dict(
                    size: int, field: str = field, make_dict: Callable = make_dict
                ) -> bool:
                    data[field] = make_dict(size)
                    return attempt(data) is not None

                size = best_size(check_dict, len(items))
                data[field] = make_dict(size)
                return serialize(data)

        # Phase 2: drop fields entirely.
        for field in self._max_bytes_trim_order(data, required):
            if field not in data:
                continue
            del data[field]
            report["dropped_fields"].append(field)
            result = attempt(data)
            if result is not None:
                return result

        # Even the minimal record does not fit: fail loudly instead of retrying.
        minimal = serialize(data)
        raise MaxBytesExceededError(max_bytes, len(minimal.encode("utf-8")))

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Extract fields from a LogRecord for logging

        This method can be overridden to implement custom logic for adding fields.

        Args:
            log_data: data that will be logged
            record: the record to extract data from
            message_dict: dictionary that was logged instead of a message. e.g
                `logger.info({"is_this_message_dict": True})`

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        for field in self.defaults:
            log_data[self._get_rename(field)] = self.defaults[field]

        for field in self._required_fields:
            log_data[self._get_rename(field)] = record.__dict__.get(field)

        for data_dict in [self.static_fields, message_dict]:
            for key, value in data_dict.items():
                log_data[self._get_rename(key)] = value

        merge_record_extra(
            record,
            log_data,
            reserved=self._skip_fields,
            rename_fields=self.rename_fields,
        )

        if self.timestamp:
            key = self.timestamp if isinstance(self.timestamp, str) else "timestamp"
            log_data[self._get_rename(key)] = datetime.fromtimestamp(
                record.created, tz=timezone.utc
            )

        if self.rename_fields_keep_missing:
            for field in self.rename_fields.values():
                if field not in log_data:
                    log_data[field] = None
        return

    def _get_rename(self, key: str) -> str:
        return self.rename_fields.get(key, key)

    # Child Methods
    # ..........................................................................
    def jsonify_log_record(self, log_data: LogData) -> str:
        """Convert the log data into a JSON string.

        Child classes MUST override this method.

        Args:
            log_data: the data to serialize

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        raise NotImplementedError()

    def process_log_record(self, log_data: LogData) -> LogData:
        """Custom processing of the data to be logged.

        Child classes can override this method to alter the log record before it
        is serialized.

        Args:
            log_data: incoming data

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        return log_data

    def formatException(self, ei) -> str | list[str]:  # type: ignore[override]
        """Format and return the specified exception information.

        If exc_info_as_array is set to True, This method returns an array of strings.
        """
        exception_info_str = super().formatException(ei)
        return exception_info_str.splitlines() if self.exc_info_as_array else exception_info_str

    def formatStack(self, stack_info) -> str | list[str]:  # type: ignore[override]
        """Format and return the specified stack information.

        If stack_info_as_array is set to True, This method returns an array of strings.
        """
        stack_info_str = super().formatStack(stack_info)
        return stack_info_str.splitlines() if self.stack_info_as_array else stack_info_str
