"""Tests for the RFC 5424 syslog formatter"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import io
import json
import logging
from collections.abc import Generator

## Installed
import pytest

## Application
from pythonjsonlogger.syslog import Rfc5424Formatter, Rfc5424ParseError, parse_rfc5424


### SETUP
### ============================================================================
_LOGGER_COUNT = 0


class LoggingEnvironment:
    def __init__(self) -> None:
        global _LOGGER_COUNT  # pylint: disable=global-statement
        _LOGGER_COUNT += 1
        self.logger = logging.getLogger(f"pythonjsonlogger.tests.syslog.{_LOGGER_COUNT}")
        self.logger.setLevel(logging.DEBUG)
        self.buffer = io.StringIO()
        self.handler = logging.StreamHandler(self.buffer)
        self.logger.addHandler(self.handler)
        return

    def set_formatter(self, formatter: logging.Formatter) -> None:
        self.handler.setFormatter(formatter)
        return

    def load_json(self):
        return json.loads(self.buffer.getvalue())

    def close(self) -> None:
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(logging.NOTSET)
        self.buffer.close()
        return


@pytest.fixture
def env() -> Generator[LoggingEnvironment]:
    environment = LoggingEnvironment()
    yield environment
    environment.close()
    return


### TESTS
### ============================================================================
def test_full_message_with_adjacent_structured_data_elements(env: LoggingEnvironment):
    "A complete message with multiple adjacent SD elements expands to full JSON output"
    env.set_formatter(Rfc5424Formatter("levelname,message", style=","))
    env.logger.info(
        "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 123 ID47 "
        '[exampleSDID@32473 iut="3" eventSource="Application" eventID="1011"]'
        '[examplePriority@32473 class="high"] '
        "BOMAn application event log entry..."
    )

    log_json = env.load_json()
    assert log_json == {
        "levelname": "INFO",
        "message": (
            "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 123 ID47 "
            '[exampleSDID@32473 iut="3" eventSource="Application" eventID="1011"]'
            '[examplePriority@32473 class="high"] '
            "BOMAn application event log entry..."
        ),
        "syslog": {
            "pri": 34,
            "facility": 4,
            "severity": 2,
            "version": 1,
            "timestamp": "2003-10-11T22:14:15.003Z",
            "hostname": "mymachine.example.com",
            "app_name": "su",
            "procid": "123",
            "msgid": "ID47",
            "structured_data": [
                {
                    "sd_id": "exampleSDID@32473",
                    "params": [
                        {"name": "iut", "value": "3"},
                        {"name": "eventSource", "value": "Application"},
                        {"name": "eventID", "value": "1011"},
                    ],
                },
                {
                    "sd_id": "examplePriority@32473",
                    "params": [{"name": "class", "value": "high"}],
                },
            ],
            "message": "BOMAn application event log entry...",
        },
    }
    return


def test_source_metadata_does_not_impersonate_record(env: LoggingEnvironment):
    "The source's hostname and severity stay inside the syslog object"
    env.set_formatter(Rfc5424Formatter("levelname,message", style=","))
    env.logger.warning("<13>1 - spoofed.example.com - - - - hello")

    log_json = env.load_json()
    # local record metadata is untouched
    assert log_json["levelname"] == "WARNING"
    assert "hostname" not in log_json
    assert "severity" not in log_json
    # source claimed metadata is nested
    assert log_json["syslog"]["hostname"] == "spoofed.example.com"
    assert log_json["syslog"]["severity"] == 5
    return


def test_nilvalue_fields(env: LoggingEnvironment):
    "NILVALUE header fields become None and NILVALUE structured data becomes an empty list"
    env.set_formatter(Rfc5424Formatter("message", style=","))
    env.logger.info("<13>1 - - - - - - no metadata here")

    log_json = env.load_json()
    assert log_json["syslog"] == {
        "pri": 13,
        "facility": 1,
        "severity": 5,
        "version": 1,
        "timestamp": None,
        "hostname": None,
        "app_name": None,
        "procid": None,
        "msgid": None,
        "structured_data": [],
        "message": "no metadata here",
    }
    return


def test_message_body_with_newlines_and_bom(env: LoggingEnvironment):
    "The body may contain newlines and a leading UTF-8 BOM is removed"
    env.set_formatter(Rfc5424Formatter("message", style=","))
    env.logger.info(
        "<13>1 2003-08-24T05:14:15.000003-07:00 host app 1 ID1 - \ufefffirst line\nsecond line"
    )

    log_json = env.load_json()
    # source timestamp precision and timezone offset are preserved
    assert log_json["syslog"]["timestamp"] == "2003-08-24T05:14:15.000003-07:00"
    assert log_json["syslog"]["message"] == "first line\nsecond line"
    return


def test_escaped_characters_in_param_values():
    r"Escaped quotes, backslashes and brackets must not truncate a PARAM-VALUE"
    parsed = parse_rfc5424(
        r'<13>1 - - - - - [esc key="quote:\" backslash:\\ bracket:\] end"] body'
    )
    assert parsed["structured_data"] == [
        {
            "sd_id": "esc",
            "params": [{"name": "key", "value": 'quote:" backslash:\\ bracket:] end'}],
        }
    ]
    assert parsed["message"] == "body"
    return


def test_duplicate_structured_data_is_preserved():
    "Repeated SD-IDs and PARAM-NAMEs are kept in order, not overwritten"
    parsed = parse_rfc5424('<13>1 - - - - - [dup a="1" a="2"][dup a="3"] -')
    assert parsed["structured_data"] == [
        {"sd_id": "dup", "params": [{"name": "a", "value": "1"}, {"name": "a", "value": "2"}]},
        {"sd_id": "dup", "params": [{"name": "a", "value": "3"}]},
    ]
    return


def test_strict_mode_raises_on_invalid_pri():
    "Strict mode rejects an out of range PRI and reports the byte position"
    formatter = Rfc5424Formatter("message", style=",")
    secret = "top-secret-message-body"
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "<999>1 - - - - - - %s", (secret,), None
    )
    with pytest.raises(Rfc5424ParseError) as exc_info:
        formatter.format(record)

    assert exc_info.value.position == 1
    assert "byte 1" in str(exc_info.value)
    # the error must not leak the message, which may contain secrets
    assert secret not in str(exc_info.value)
    return


def test_invalid_pri_variants():
    "Malformed PRI values are rejected"
    for message in [
        "<>1 - - - - - - x",
        "<1a>1 - - - - - - x",
        "<1234>1 - - - - - - x",
        "13>1 - - - - - - x",
    ]:
        with pytest.raises(Rfc5424ParseError):
            parse_rfc5424(message)
    return


def test_truncated_escape_in_param_value():
    "A backslash at the end of input is a truncated escape"
    with pytest.raises(Rfc5424ParseError) as exc_info:
        parse_rfc5424('<13>1 - - - - - [a b="value\\')
    assert "truncated escape" in str(exc_info.value)
    assert exc_info.value.position > 0
    return


def test_unterminated_param_value():
    with pytest.raises(Rfc5424ParseError):
        parse_rfc5424('<13>1 - - - - - [a b="value')
    return


def test_field_length_limits():
    "Fields exceeding their RFC 5424 length limits are rejected"
    with pytest.raises(Rfc5424ParseError):
        # APP-NAME is limited to 48 characters
        parse_rfc5424(f"<13>1 - - {'a' * 49} - - - x")
    with pytest.raises(Rfc5424ParseError):
        # MSGID is limited to 32 characters
        parse_rfc5424(f"<13>1 - - - - {'a' * 33} - x")
    return


def test_non_strict_mode_keeps_original_message(env: LoggingEnvironment):
    "Non-strict mode logs the original message unchanged when parsing fails"
    env.set_formatter(Rfc5424Formatter("levelname,message", style=",", strict=False))
    env.logger.info("this is not a syslog message")

    log_json = env.load_json()
    assert log_json == {"levelname": "INFO", "message": "this is not a syslog message"}
    return


def test_non_strict_mode_still_parses_valid_messages(env: LoggingEnvironment):
    env.set_formatter(Rfc5424Formatter("message", style=",", strict=False))
    env.logger.info("<13>1 - - - - - - hello")

    log_json = env.load_json()
    assert log_json["syslog"]["message"] == "hello"
    return


def test_custom_syslog_field_name(env: LoggingEnvironment):
    env.set_formatter(Rfc5424Formatter("message", style=",", syslog_field="syslog5424"))
    env.logger.info("<13>1 - - - - - - hello")

    log_json = env.load_json()
    assert log_json["syslog5424"]["pri"] == 13
    assert "syslog" not in log_json
    return


def test_missing_message_body():
    "A message without a MSG part has no body"
    parsed = parse_rfc5424("<13>1 - - - - - -")
    assert parsed["message"] is None
    assert parsed["structured_data"] == []
    return
