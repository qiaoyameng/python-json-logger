"""RFC 5424 syslog message support.

This module provides an opt-in formatter, [`SyslogFormatter`][pythonjsonlogger.syslog.SyslogFormatter],
for deployments where a gateway hands raw RFC 5424 syslog messages to `logging`.
Instead of emitting the whole syslog line as a single opaque `message` string, the
formatter parses it and expands the header fields, structured data, and message body
into a dedicated `syslog` object in the JSON output.

The parsed values are claims made by the *source* of the syslog message. They are
therefore kept inside the `syslog` object and never overwrite trusted local
`LogRecord` metadata such as `levelname`.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from dataclasses import dataclass, field
import logging
import re
from typing import Any

## Application
from .exception import PythonJsonLoggerError
from .json import JsonFormatter


### CONSTANTS
### ============================================================================
NILVALUE = "-"
"""RFC 5424 NILVALUE: a field with no value."""

_MAX_PRI = 191
_MAX_HOSTNAME_LENGTH = 255
_MAX_APP_NAME_LENGTH = 48
_MAX_PROCID_LENGTH = 128
_MAX_MSGID_LENGTH = 32
_MAX_SD_NAME_LENGTH = 32

# PRINTUSASCII = %d33-126 (printable ASCII excluding SP)
_PRINTUSASCII = frozenset(chr(c) for c in range(33, 127))
# SD-NAME excludes `=`, `]`, and `"` as well as SP
_SD_NAME_CHARS = _PRINTUSASCII - {'=', ']', '"'}
# Characters that must be escaped inside a PARAM-VALUE
_SD_ESCAPABLE = {'"', "\\", "]"}

_TIMESTAMP_REGEX = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


### CLASSES
### ============================================================================
class SyslogParseError(PythonJsonLoggerError, ValueError):
    """An RFC 5424 syslog message could not be parsed.

    Attributes:
        position: byte offset (UTF-8) into the message at which parsing failed.

    Note:
        The error message deliberately does not include the syslog message itself,
        as it may contain secrets.
    """

    def __init__(self, detail: str, position: int) -> None:
        self.position = position
        super().__init__(f"invalid RFC 5424 syslog message: {detail} (byte {position})")
        return


@dataclass
class SyslogMessage:
    """A parsed RFC 5424 syslog message.

    `timestamp`, `hostname`, `app_name`, `procid`, and `msgid` are `None` when the
    corresponding field was the NILVALUE (`-`). `timestamp` is kept as the raw
    source string so the original precision and timezone are preserved.

    `structured_data` is a list of elements (in source order); each element's
    `params` is a list of `[name, value]` pairs (also in source order) so repeated
    elements or parameter names are preserved instead of silently overwritten.
    """

    priority: int
    facility: int
    severity: int
    version: int
    timestamp: str | None
    hostname: str | None
    app_name: str | None
    procid: str | None
    msgid: str | None
    structured_data: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this message."""
        return {
            "priority": self.priority,
            "facility": self.facility,
            "severity": self.severity,
            "version": self.version,
            "timestamp": self.timestamp,
            "hostname": self.hostname,
            "app_name": self.app_name,
            "procid": self.procid,
            "msgid": self.msgid,
            "structured_data": [
                {"id": element["id"], "params": [list(param) for param in element["params"]]}
                for element in self.structured_data
            ],
            "message": self.message,
        }


class _Parser:
    """Cursor-based parser tracking character and UTF-8 byte positions."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        return

    def byte_offset(self, pos: int | None = None) -> int:
        char_pos = self.pos if pos is None else pos
        return len(self.text[:char_pos].encode("utf-8"))

    def error(self, detail: str, pos: int | None = None) -> SyslogParseError:
        return SyslogParseError(detail, self.byte_offset(pos))

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self) -> str | None:
        return None if self.at_end() else self.text[self.pos]

    def expect_space(self, context: str) -> None:
        if self.peek() != " ":
            raise self.error(f"expected SP after {context}")
        self.pos += 1
        return


### FUNCTIONS
### ============================================================================
def _parse_pri(parser: _Parser) -> int:
    if parser.peek() != "<":
        raise parser.error("expected '<' starting PRI")
    end = parser.text.find(">", parser.pos + 1)
    if end == -1:
        raise parser.error("unterminated PRI", len(parser.text))
    digits = parser.text[parser.pos + 1 : end]
    if not digits or len(digits) > 3 or not digits.isdigit():
        raise parser.error("PRI must be 1-3 digits", parser.pos + 1)
    priority = int(digits)
    if priority > _MAX_PRI:
        raise parser.error(f"PRI value {priority} out of range 0-{_MAX_PRI}", parser.pos + 1)
    parser.pos = end + 1
    return priority


def _parse_version(parser: _Parser) -> int:
    start = parser.pos
    while not parser.at_end() and parser.text[parser.pos].isdigit():
        parser.pos += 1
    digits = parser.text[start : parser.pos]
    if not digits or digits[0] == "0" or len(digits) > 3:
        raise parser.error("VERSION must be a non-zero digit followed by up to 2 digits", start)
    return int(digits)


def _parse_token(parser: _Parser, name: str, max_length: int) -> str | None:
    """Parse a NILVALUE-or-PRINTUSASCII header field terminated by SP."""
    start = parser.pos
    while not parser.at_end() and parser.text[parser.pos] != " ":
        if parser.text[parser.pos] not in _PRINTUSASCII:
            raise parser.error(f"invalid character in {name}")
        parser.pos += 1
    token = parser.text[start : parser.pos]
    if not token:
        raise parser.error(f"empty {name} field", start)
    if token == NILVALUE:
        return None
    if len(token) > max_length:
        raise parser.error(f"{name} exceeds {max_length} characters", start)
    return token


def _parse_timestamp(parser: _Parser) -> str | None:
    token = _parse_token(parser, "TIMESTAMP", max_length=32)
    if token is None:
        return None
    if not _TIMESTAMP_REGEX.fullmatch(token):
        raise parser.error("malformed TIMESTAMP", parser.pos - len(token))
    return token


def _parse_sd_name(parser: _Parser, context: str) -> str:
    start = parser.pos
    while not parser.at_end() and parser.text[parser.pos] in _SD_NAME_CHARS:
        parser.pos += 1
    name = parser.text[start : parser.pos]
    if not name:
        raise parser.error(f"expected {context}", start)
    if len(name) > _MAX_SD_NAME_LENGTH:
        raise parser.error(f"{context} exceeds {_MAX_SD_NAME_LENGTH} characters", start)
    return name


def _parse_sd_param_value(parser: _Parser) -> str:
    if parser.peek() != '"':
        raise parser.error("expected '\"' starting PARAM-VALUE")
    parser.pos += 1
    chars: list[str] = []
    while True:
        if parser.at_end():
            raise parser.error("unterminated PARAM-VALUE")
        char = parser.text[parser.pos]
        if char == "\\":
            if parser.pos + 1 >= len(parser.text):
                raise parser.error("truncated escape sequence in PARAM-VALUE")
            following = parser.text[parser.pos + 1]
            if following in _SD_ESCAPABLE:
                chars.append(following)
            else:
                # Not an escape sequence: keep both characters (RFC 5424 section 6.3.3)
                chars.append(char)
                chars.append(following)
            parser.pos += 2
        elif char == '"':
            parser.pos += 1
            return "".join(chars)
        else:
            chars.append(char)
            parser.pos += 1


def _parse_sd_element(parser: _Parser) -> dict[str, Any]:
    parser.pos += 1  # consume '['
    sd_id = _parse_sd_name(parser, "SD-ID")
    params: list[list[str]] = []
    while parser.peek() == " ":
        parser.pos += 1
        param_name = _parse_sd_name(parser, "PARAM-NAME")
        if parser.peek() != "=":
            raise parser.error("expected '=' after PARAM-NAME")
        parser.pos += 1
        params.append([param_name, _parse_sd_param_value(parser)])
    if parser.peek() != "]":
        if parser.at_end():
            raise parser.error("unterminated SD-ELEMENT")
        raise parser.error("expected ']' closing SD-ELEMENT")
    parser.pos += 1
    return {"id": sd_id, "params": params}


def _parse_structured_data(parser: _Parser) -> list[dict[str, Any]]:
    if parser.peek() == NILVALUE:
        parser.pos += 1
        return []
    elements: list[dict[str, Any]] = []
    while parser.peek() == "[":
        elements.append(_parse_sd_element(parser))
    if not elements:
        raise parser.error("expected STRUCTURED-DATA")
    return elements


def parse_rfc5424(message: str) -> SyslogMessage:
    """Parse an RFC 5424 syslog message.

    Args:
        message: the raw syslog message (without any transport framing).

    Returns:
        the parsed [`SyslogMessage`][pythonjsonlogger.syslog.SyslogMessage].

    Raises:
        SyslogParseError: if the message does not conform to RFC 5424. The error
            carries the UTF-8 byte `position` of the failure but never the message
            content.
    """
    parser = _Parser(message)

    priority = _parse_pri(parser)
    version = _parse_version(parser)
    parser.expect_space("VERSION")

    timestamp = _parse_timestamp(parser)
    parser.expect_space("TIMESTAMP")

    hostname = _parse_token(parser, "HOSTNAME", _MAX_HOSTNAME_LENGTH)
    parser.expect_space("HOSTNAME")

    app_name = _parse_token(parser, "APP-NAME", _MAX_APP_NAME_LENGTH)
    parser.expect_space("APP-NAME")

    procid = _parse_token(parser, "PROCID", _MAX_PROCID_LENGTH)
    parser.expect_space("PROCID")

    msgid = _parse_token(parser, "MSGID", _MAX_MSGID_LENGTH)
    parser.expect_space("MSGID")

    structured_data = _parse_structured_data(parser)

    body = ""
    if not parser.at_end():
        parser.expect_space("STRUCTURED-DATA")
        body = parser.text[parser.pos :]
        if body.startswith("\ufeff"):
            body = body[1:]

    return SyslogMessage(
        priority=priority,
        facility=priority // 8,
        severity=priority % 8,
        version=version,
        timestamp=timestamp,
        hostname=hostname,
        app_name=app_name,
        procid=procid,
        msgid=msgid,
        structured_data=structured_data,
        message=body,
    )


class SyslogFormatter(JsonFormatter):
    """JSON formatter that expands RFC 5424 syslog messages into structured fields.

    The record's message is parsed as an RFC 5424 syslog message and the result is
    added under the `syslog` field. Fields claimed by the source message (such as
    `hostname` and `severity`) stay inside the `syslog` object and never overwrite
    trusted local `LogRecord` metadata.

    *New in 4.2.1*
    """

    def __init__(
        self,
        *args: Any,
        on_parse_error: str = "strict",
        syslog_field: str = "syslog",
        **kwargs: Any,
    ) -> None:
        """
        Args:
            args: see [JsonFormatter][pythonjsonlogger.json.JsonFormatter]
            on_parse_error: failure strategy when the message is not valid RFC 5424.
                `"strict"` raises [SyslogParseError][pythonjsonlogger.syslog.SyslogParseError];
                `"preserve"` leaves the original message unexpanded.
            syslog_field: name of the field the parsed message is stored under.
            kwargs: see [JsonFormatter][pythonjsonlogger.json.JsonFormatter]
        """
        super().__init__(*args, **kwargs)
        if on_parse_error not in ("strict", "preserve"):
            raise ValueError("on_parse_error must be 'strict' or 'preserve'")
        self.on_parse_error = on_parse_error
        self.syslog_field = syslog_field
        return

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        try:
            parsed = parse_rfc5424(record.message)
        except SyslogParseError:
            if self.on_parse_error == "strict":
                raise
            return
        log_data[self._get_rename(self.syslog_field)] = parsed.to_dict()
        return
