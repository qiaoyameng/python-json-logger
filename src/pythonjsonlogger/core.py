"""Core functionality shared by all JSON loggers"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import logging
import re
import string
import sys
from typing import TypeAlias, Any
from collections.abc import Container, Mapping, Sequence

## Installed

## Application


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


class FieldSource(str, Enum):
    """Sources from which a log field value can originate."""

    DEFAULTS = "defaults"
    REQUIRED_FIELDS = "required_fields"
    STATIC_FIELDS = "static_fields"
    MESSAGE = "message"
    EXTRA = "extra"
    TIMESTAMP = "timestamp"


class FieldConflictStrategy(str, Enum):
    """Strategies used when more than one source provides the same field."""

    OVERRIDE = "override"
    PRIORITY = "priority"
    KEEP_ALL = "keep_all"
    ERROR = "error"


FIELD_SOURCE_ORDER: tuple[FieldSource, ...] = (
    FieldSource.DEFAULTS,
    FieldSource.REQUIRED_FIELDS,
    FieldSource.STATIC_FIELDS,
    FieldSource.MESSAGE,
    FieldSource.EXTRA,
    FieldSource.TIMESTAMP,
)
"""Default source precedence, from lowest to highest.

This order preserves the formatter's historical step-by-step override behaviour.
"""


class FieldValues(list[Any]):
    """A list containing every value supplied for a conflicting field.

    The list subclass is serialized as a plain JSON array, so retained values do
    not occupy a user-supplied object key.
    """

    def __init__(
        self,
        values: Sequence[Any] = (),
        sources: Sequence[FieldSource | str] = (),
    ) -> None:
        super().__init__(values)
        self.sources = tuple(
            source.value if isinstance(source, FieldSource) else str(source)
            for source in sources
        )


class FieldConflictError(ValueError):
    """Raised when field sources conflict and the selected strategy is `error`.

    The error message intentionally contains field names and sources, but never
    the conflicting values.
    """

    def __init__(self, conflicts: Mapping[Any, Sequence[FieldSource | str]]) -> None:
        self.conflicts = {
            key: [
                source.value if isinstance(source, FieldSource) else str(source)
                for source in sources
            ]
            for key, sources in conflicts.items()
        }

        descriptions = []
        for key in sorted(self.conflicts, key=_field_sort_key):
            sources = ", ".join(self.conflicts[key])
            descriptions.append(f"{key!r} (sources: {sources})")
        super().__init__("Conflicting log field values: " + "; ".join(descriptions))


@dataclass(frozen=True)
class _FieldCandidate:
    source: FieldSource
    original_field: Any
    value: Any


ResolvedConflictStrategy: TypeAlias = FieldConflictStrategy | tuple[FieldSource | str, ...]
FieldConflictStrategies: TypeAlias = (
    Mapping[Any, FieldConflictStrategy | Sequence[FieldSource | str] | str | None]
    | None
)


def _field_sort_key(field: Any) -> tuple[str, str]:
    return (type(field).__name__, str(field))


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
        field_conflict_strategy: ResolvedConflictStrategy | str | None = None,
        field_conflict_strategies: FieldConflictStrategies = None,
        source_priority: Sequence[FieldSource | str] | None = None,
        include_field_sources: bool = False,
        exc_info_as_array: bool = False,
        stack_info_as_array: bool = False,
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
            field_conflict_strategy: strategy for fields supplied by more than one source.
                Defaults to `FieldConflictStrategy.OVERRIDE`, which preserves the historical
                precedence defaults, required fields, static fields, message, extra and
                timestamp. `PRIORITY` uses `source_priority`, `KEEP_ALL` retains every value,
                and `ERROR` raises `FieldConflictError`. A sequence of source names may also
                be supplied to select by that priority.
            field_conflict_strategies: per-output-field overrides for
                `field_conflict_strategy`. Fields are identified after renaming.
            source_priority: order of field sources from lowest to highest precedence.
            include_field_sources: when retaining all values, serialize each value as a
                compact `(source, value)` pair.
            exc_info_as_array: break the exc_info into a list of lines based on line breaks.
            stack_info_as_array: break the stack_info into a list of lines based on line breaks.

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
        self.source_priority = self._normalize_source_priority(source_priority)
        self.field_conflict_strategy = self._normalize_conflict_strategy(
            field_conflict_strategy, require_source_priority=True
        )
        self.field_conflict_strategies = {}
        if field_conflict_strategies is not None:
            for key, strategy in field_conflict_strategies.items():
                needs_source_priority = (
                    (
                        type(strategy) is FieldConflictStrategy
                        and strategy == FieldConflictStrategy.PRIORITY
                        and self.source_priority is None
                    )
                )
                self.field_conflict_strategies[key] = self._normalize_conflict_strategy(
                    strategy, require_source_priority=needs_source_priority
                )
        self.include_field_sources = include_field_sources

        self._skip_fields = set(self._required_fields)
        self._skip_fields.update(self.reserved_attrs)
        self.defaults = defaults if defaults is not None else {}
        self.exc_info_as_array = exc_info_as_array
        self.stack_info_as_array = stack_info_as_array
        return

    @staticmethod
    def _normalize_source_priority(
        source_priority: Sequence[FieldSource | str] | None,
    ) -> tuple[FieldSource, ...] | None:
        if source_priority is None:
            return None

        normalized = tuple(
            source if isinstance(source, FieldSource) else FieldSource(source)
            for source in source_priority
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("source_priority must not contain duplicate sources")
        if set(normalized) != set(FIELD_SOURCE_ORDER):
            raise ValueError("source_priority must contain every field source exactly once")
        return normalized

    def _normalize_conflict_strategy(
        self,
        strategy: ResolvedConflictStrategy | str | None,
        require_source_priority: bool = True,
    ) -> ResolvedConflictStrategy:
        if strategy is None:
            return FieldConflictStrategy.OVERRIDE

        if type(strategy) is FieldConflictStrategy:
            resolved = strategy
        elif isinstance(strategy, Sequence) and not isinstance(strategy, (bytes, str)):
            sources = tuple(
                source.value if isinstance(source, FieldSource) else str(source)
                for source in strategy
            )
            if len(sources) != len(set(sources)):
                raise ValueError("source priority must not contain duplicate sources")
            for source in sources:
                FieldSource(source)
            resolved = sources
        elif isinstance(strategy, str):
            resolved = FieldConflictStrategy(strategy)
        else:
            raise ValueError("Invalid field conflict strategy")

        if (
            type(resolved) is FieldConflictStrategy
            and resolved == FieldConflictStrategy.PRIORITY
            and require_source_priority
            and self.source_priority is None
        ):
            raise ValueError("source_priority is required when using the priority strategy")
        return resolved

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
        """
        return self.prefix + self.jsonify_log_record(log_data)

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
        candidates: dict[Any, list[_FieldCandidate]] = {}

        def add_candidate(source: FieldSource, original_field: Any, value: Any) -> None:
            output_field = self._get_rename(original_field)
            candidates.setdefault(output_field, []).append(
                _FieldCandidate(source, original_field, value)
            )

        for field, value in self.defaults.items():
            add_candidate(FieldSource.DEFAULTS, field, value)

        for field in self._required_fields:
            add_candidate(FieldSource.REQUIRED_FIELDS, field, record.__dict__.get(field))

        for field, value in self.static_fields.items():
            add_candidate(FieldSource.STATIC_FIELDS, field, value)

        for field, value in message_dict.items():
            add_candidate(FieldSource.MESSAGE, field, value)

        for field, value in record.__dict__.items():
            if field not in self._skip_fields and not (
                hasattr(field, "startswith") and field.startswith("_")
            ):
                add_candidate(FieldSource.EXTRA, field, value)

        if self.timestamp:
            field = self.timestamp if isinstance(self.timestamp, str) else "timestamp"
            add_candidate(
                FieldSource.TIMESTAMP,
                field,
                datetime.fromtimestamp(record.created, tz=timezone.utc),
            )

        log_data.update(self._resolve_field_candidates(candidates))

        if self.rename_fields_keep_missing:
            for field in self.rename_fields.values():
                if field not in log_data:
                    log_data[field] = None
        return

    def _resolve_field_candidates(
        self, candidates: Mapping[Any, Sequence[_FieldCandidate]]
    ) -> dict[Any, Any]:
        default_order = {source: index for index, source in enumerate(FIELD_SOURCE_ORDER)}
        source_priority = self.source_priority
        custom_priority_order = (
            {source: index for index, source in enumerate(source_priority)}
            if source_priority is not None
            else {}
        )
        resolved: dict[Any, Any] = {}
        conflicts: dict[Any, list[str]] = {}

        for output_field, field_candidates in candidates.items():
            if len(field_candidates) == 1:
                resolved[output_field] = field_candidates[0].value
                continue

            sources = {candidate.source for candidate in field_candidates}
            ordered_candidates = sorted(
                field_candidates,
                key=lambda candidate: self._candidate_sort_key(candidate, default_order),
            )
            strategy = self.field_conflict_strategies.get(
                output_field, self.field_conflict_strategy
            )

            if strategy is FieldConflictStrategy.OVERRIDE:
                resolved[output_field] = ordered_candidates[-1].value
            elif strategy is FieldConflictStrategy.PRIORITY:
                priority_order = custom_priority_order or {
                    source: rank for source, rank in default_order.items()
                }
                resolved[output_field] = sorted(
                    ordered_candidates,
                    key=lambda candidate: self._candidate_priority_key(candidate, priority_order),
                )[-1].value
            elif strategy is FieldConflictStrategy.KEEP_ALL:
                values = FieldValues(
                    [candidate.value for candidate in ordered_candidates],
                    [candidate.source for candidate in ordered_candidates],
                )
                if self.include_field_sources:
                    values = FieldValues(
                        [
                            [candidate.source.value, candidate.value]
                            for candidate in ordered_candidates
                        ],
                        [candidate.source for candidate in ordered_candidates],
                    )
                resolved[output_field] = values
            elif strategy is FieldConflictStrategy.ERROR:
                conflicts[output_field] = sorted(
                    {source.value for source in sources},
                )
            elif isinstance(strategy, tuple):
                priority_order = self._custom_strategy_order(strategy)
                resolved[output_field] = sorted(
                    ordered_candidates,
                    key=lambda candidate: self._candidate_priority_key(candidate, priority_order),
                )[-1].value

        if conflicts:
            raise FieldConflictError(conflicts)
        return resolved

    @staticmethod
    def _candidate_sort_key(
        candidate: _FieldCandidate,
        default_order: Mapping[FieldSource, int],
    ) -> tuple[int, str, str]:
        return (
            default_order[candidate.source],
            type(candidate.original_field).__name__,
            str(candidate.original_field),
        )

    @staticmethod
    def _candidate_priority_key(
        candidate: _FieldCandidate,
        priority_order: Mapping[FieldSource | str, int],
    ) -> tuple[int, int, int, str, str]:
        if candidate.source in priority_order:
            explicit_rank = priority_order[candidate.source]
            configured = 1
        else:
            explicit_rank = -1
            configured = 0
        return (
            configured,
            explicit_rank if configured else -1,
            FIELD_SOURCE_ORDER.index(candidate.source),
            type(candidate.original_field).__name__,
            str(candidate.original_field),
        )

    @staticmethod
    def _custom_strategy_order(
        strategy: tuple[str, ...],
    ) -> dict[FieldSource | str, int]:
        sources = tuple(FieldSource(source).value for source in strategy)
        if len(sources) != len(set(sources)):
            raise ValueError("source priority must not contain duplicate sources")
        unknown_sources = set(sources) - {source.value for source in FIELD_SOURCE_ORDER}
        if unknown_sources:
            raise ValueError(f"Unknown field sources: {', '.join(sorted(unknown_sources))}")
        return {FieldSource(source): index for index, source in enumerate(sources)}

    def _get_rename(self, key: Any) -> Any:
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
