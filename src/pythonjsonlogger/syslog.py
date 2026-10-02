"""Opt-in formatter that expands RFC 5424 syslog messages into structured fields.

Gateways commonly forward [RFC 5424](https://www.rfc-editor.org/rfc/rfc5424) syslog
messages as a single opaque string, leaving JSON logs with one long `message` field.
The `Rfc5424Formatter` provided here parses such messages and exposes the header
fields, structured data, and message body as a nested object.

Parsed values are placed under a dedicated field (`syslog` by default) so that data
claimed by the *source* of the syslog message (e.g. its hostname or severity) is never
confused with the trusted metadata of the local `LogRecord` (e.g. `levelname`).
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import logging
import re
from typing import Any

## Installed

## Application
from .exception import PythonJsonLoggerError
from .json import JsonFormatter


### CONSTANTS
### ============================================================================
NILVALUE = "-"
"""The RFC 5424 NILVALUE, used when a field has no value."""

MAX_HOSTNAME_LENGTH = 255
MAX_APP_NAME_LENGTH = 48
MAX_PROCID_LENGTH = 128
MAX_MSGID_LENGTH = 32
MAX_SD_NAME_LENGTH = 32
MAX_PRI_VALUE = 191

_VERSION_REGEX = re.compile(r"[1-9][0-9]? ")
_TIMESTAMP_REGEX = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_ASCII_DIGITS = frozenset("0123456789")
_SD_NAME_DELIMITERS = frozenset(' ]="')
_PARAM_VALUE_ESCAPED_CHARS = frozenset('\\"]')
_UTF8_BOM = "\ufeff"


### CLASSES
### ============================================================================
class Rfc5424ParseError(PythonJsonLoggerError, ValueError):
    """An RFC 5424 syslog message could not be parsed.

    The error reports the byte position of the problem. The message itself is never
    included as it may contain secrets.
    """

    def __init__(self, reason: str, position: int) -> None:
        """
        Args:
            reason: what went wrong (must not contain message content)
            position: byte offset into the message where the problem was found
        """
        self.reason = reason
        self.position = position
        super().__init__(f"invalid RFC 5424 syslog message at byte {position}: {reason}")
        return


### FUNCTIONS
### ============================================================================
def parse_rfc5424(message: str) -> dict[str, Any]:
    """Parse an RFC 5424 syslog message into its component fields.

    Args:
        message: the raw syslog message

    Returns:
        A dictionary with the keys `pri`, `facility`, `severity`, `version`,
        `timestamp`, `hostname`, `app_name`, `procid`, `msgid`, `structured_data`,
        and `message`.

        Fields that carry the NILVALUE (`-`) are `None`, except `structured_data`
        which is an empty list. `timestamp` is kept as the original string so the
        source's precision and timezone offset are preserved. `structured_data` is a
        list of `{"sd_id": ..., "params": ...}` elements in their original order, where
        `params` is a list of `{"name": ..., "value": ...}` entries, also in order, so
        repeated elements or parameter names are preserved rather than overwritten.
        `message` is the message body with any UTF-8 BOM removed, or `None` if the
        syslog message carries no body.

    Raises:
        Rfc5424ParseError: the message does not conform to RFC 5424
    """
    if not isinstance(message, str):
        raise TypeError(f"message must be str, not {type(message).__name__}")
    return _Parser(message).parse()


class Rfc5424Formatter(JsonFormatter):
    """JSON formatter that expands RFC 5424 syslog messages into structured fields.

    The record's message is parsed with [parse_rfc5424][pythonjsonlogger.syslog.parse_rfc5424]
    and the result is added as a nested object (`syslog` by default). Fields claimed by
    the source message, such as `hostname` and `severity`, live inside this object and
    never overwrite the local `LogRecord` attributes.
    """

    def __init__(
        self,
        *args,
        strict: bool = True,
        syslog_field: str = "syslog",
        **kwargs,
    ) -> None:
        """
        Args:
            args: see [BaseJsonFormatter][pythonjsonlogger.core.BaseJsonFormatter]
            strict: if `True` (default) unparseable messages raise
                [Rfc5424ParseError][pythonjsonlogger.syslog.Rfc5424ParseError];
                if `False` the original message is logged unchanged.
            syslog_field: name of the field the parsed data is added under
                (subject to `rename_fields`).
            kwargs: see [BaseJsonFormatter][pythonjsonlogger.core.BaseJsonFormatter]
        """
        super().__init__(*args, **kwargs)
        self.strict = strict
        self.syslog_field = syslog_field
        return

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Extract fields from a LogRecord for logging

        Args:
            log_data: data that will be logged
            record: the record to extract data from
            message_dict: dictionary that was logged instead of a message
        """
        super().add_fields(log_data, record, message_dict)
        try:
            parsed = parse_rfc5424(record.getMessage())
        except Rfc5424ParseError:
            if self.strict:
                raise
            # keep the original message untouched
            return
        log_data[self._get_rename(self.syslog_field)] = parsed
        return


### INTERNAL
### ============================================================================
def _is_printusascii(text: str) -> bool:
    return all(33 <= ord(char) <= 126 for char in text)


class _Parser:
    """Index based parser for a single RFC 5424 message."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.length = len(message)
        self.pos = 0
        return

    def fail(self, reason: str, position: int | None = None) -> None:
        index = self.pos if position is None else position
        byte_position = len(self.message[:index].encode("utf-8"))
        raise Rfc5424ParseError(reason, byte_position)

    def parse(self) -> dict[str, Any]:
        pri = self.parse_pri()
        version = self.parse_version()
        timestamp = self.parse_timestamp()
        hostname = self.parse_token("HOSTNAME", MAX_HOSTNAME_LENGTH)
        app_name = self.parse_token("APP-NAME", MAX_APP_NAME_LENGTH)
        procid = self.parse_token("PROCID", MAX_PROCID_LENGTH)
        msgid = self.parse_token("MSGID", MAX_MSGID_LENGTH)
        structured_data = self.parse_structured_data()
        body = self.parse_message()
        return {
            "pri": pri,
            "facility": pri // 8,
            "severity": pri % 8,
            "version": version,
            "timestamp": timestamp,
            "hostname": hostname,
            "app_name": app_name,
            "procid": procid,
            "msgid": msgid,
            "structured_data": structured_data,
            "message": body,
        }

    def parse_pri(self) -> int:
        if not self.message.startswith("<"):
            self.fail("expected '<' starting PRI", 0)
        end = self.message.find(">", 1)
        if end == -1:
            self.fail("unterminated PRI, expected '>'", 0)
        pri_text = self.message[1:end]
        if not 1 <= len(pri_text) <= 3 or any(c not in _ASCII_DIGITS for c in pri_text):
            self.fail("PRI must be 1-3 ASCII digits", 1)
        pri = int(pri_text)
        if pri > MAX_PRI_VALUE:
            self.fail(f"PRI value out of range (0-{MAX_PRI_VALUE})", 1)
        self.pos = end + 1
        return pri

    def parse_version(self) -> int:
        match = _VERSION_REGEX.match(self.message, self.pos)
        if match is None:
            self.fail("invalid VERSION")
        version = int(match.group()[:-1])
        if version != 1:
            self.fail(f"unsupported VERSION {version}")
        self.pos = match.end()
        return version

    def parse_timestamp(self) -> str | None:
        end = self.message.find(" ", self.pos)
        if end == -1:
            self.fail("expected SP after TIMESTAMP", self.length)
        token = self.message[self.pos : end]
        if token == NILVALUE:
            self.pos = end + 1
            return None
        if _TIMESTAMP_REGEX.fullmatch(token) is None:
            self.fail("invalid TIMESTAMP")
        self.pos = end + 1
        return token

    def parse_token(self, field: str, max_length: int) -> str | None:
        end = self.message.find(" ", self.pos)
        if end == -1:
            self.fail(f"expected SP after {field}", self.length)
        token = self.message[self.pos : end]
        if token == NILVALUE:
            self.pos = end + 1
            return None
        if not token:
            self.fail(f"empty {field}")
        if len(token) > max_length:
            self.fail(f"{field} exceeds {max_length} characters")
        if not _is_printusascii(token):
            self.fail(f"invalid character in {field}")
        self.pos = end + 1
        return token

    def parse_structured_data(self) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        if self.pos < self.length and self.message[self.pos] == NILVALUE:
            self.pos += 1
            return elements
        while self.pos < self.length and self.message[self.pos] == "[":
            elements.append(self.parse_sd_element())
        if not elements:
            self.fail("expected STRUCTURED-DATA element or NILVALUE")
        return elements

    def parse_sd_element(self) -> dict[str, Any]:
        self.pos += 1  # consume "["
        sd_id = self.parse_sd_name("SD-ID")
        params: list[dict[str, str]] = []
        while True:
            if self.pos >= self.length:
                self.fail("unterminated SD-ELEMENT, expected ']'", self.length)
            char = self.message[self.pos]
            if char == "]":
                self.pos += 1
                break
            if char != " ":
                self.fail("expected SP or ']' in SD-ELEMENT")
            self.pos += 1
            name = self.parse_sd_name("PARAM-NAME")
            if self.pos >= self.length or self.message[self.pos] != "=":
                self.fail("expected '=' after PARAM-NAME")
            self.pos += 1
            if self.pos >= self.length or self.message[self.pos] != '"':
                self.fail("expected '\"' starting PARAM-VALUE")
            self.pos += 1
            params.append({"name": name, "value": self.parse_param_value()})
        return {"sd_id": sd_id, "params": params}

    def parse_sd_name(self, field: str) -> str:
        start = self.pos
        while self.pos < self.length and self.message[self.pos] not in _SD_NAME_DELIMITERS:
            self.pos += 1
        name = self.message[start : self.pos]
        if not name:
            self.fail(f"empty {field}", start)
        if len(name) > MAX_SD_NAME_LENGTH:
            self.fail(f"{field} exceeds {MAX_SD_NAME_LENGTH} characters", start)
        if not _is_printusascii(name):
            self.fail(f"invalid character in {field}", start)
        return name

    def parse_param_value(self) -> str:
        chars: list[str] = []
        while True:
            if self.pos >= self.length:
                self.fail("unterminated PARAM-VALUE, expected '\"'", self.length)
            char = self.message[self.pos]
            if char == '"':
                self.pos += 1
                return "".join(chars)
            if char == "\\":
                if self.pos + 1 >= self.length:
                    self.fail("truncated escape in PARAM-VALUE")
                escaped = self.message[self.pos + 1]
                if escaped in _PARAM_VALUE_ESCAPED_CHARS:
                    chars.append(escaped)
                else:
                    # not an escape sequence: both characters are kept (RFC 5424 6.3.3)
                    chars.append(char)
                    chars.append(escaped)
                self.pos += 2
                continue
            chars.append(char)
            self.pos += 1

    def parse_message(self) -> str | None:
        if self.pos >= self.length:
            return None
        if self.message[self.pos] != " ":
            self.fail("expected SP before MSG")
        self.pos += 1
        body = self.message[self.pos :]
        if body.startswith(_UTF8_BOM):
            body = body[len(_UTF8_BOM) :]
        return body
