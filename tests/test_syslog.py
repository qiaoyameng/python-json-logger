### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from dataclasses import dataclass
import io
import json
import logging
from collections.abc import Generator

## Installed
import pytest

## Application
from pythonjsonlogger.syslog import SyslogFormatter, SyslogParseError, parse_rfc5424


### SETUP
### ============================================================================
_LOGGER_COUNT = 0


@dataclass
class LoggingEnvironment:
    logger: logging.Logger
    buffer: io.StringIO
    handler: logging.Handler

    def load_json(self) -> dict:
        return json.loads(self.buffer.getvalue())


@pytest.fixture
def env() -> Generator[LoggingEnvironment]:
    global _LOGGER_COUNT  # pylint: disable=global-statement
    _LOGGER_COUNT += 1
    logger = logging.getLogger(f"pythonjsonlogger.tests.syslog.{_LOGGER_COUNT}")
    logger.setLevel(logging.DEBUG)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    logger.addHandler(handler)
    yield LoggingEnvironment(logger=logger, buffer=buffer, handler=handler)
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    buffer.close()
    return


def make_record(message: str) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.syslog",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    return record


### PARSER TESTS
### ============================================================================
def test_parse_full_message_with_adjacent_sd_elements():
    parsed = parse_rfc5424(
        "<13>1 2003-10-11T22:14:15.003Z mymachine.example.com su 123 ID47 "
        "[exampleSDID@32473 iut=\"3\" eventSource=\"Application\" eventID=\"1011\"]"
        "[examplePriority@32473 class=\"high\"]"
        " An application event log entry..."
    )
    assert parsed.priority == 13
    assert parsed.facility == 1
    assert parsed.severity == 5
    assert parsed.version == 1
    assert parsed.timestamp == "2003-10-11T22:14:15.003Z"
    assert parsed.hostname == "mymachine.example.com"
    assert parsed.app_name == "su"
    assert parsed.procid == "123"
    assert parsed.msgid == "ID47"
    assert parsed.structured_data == [
        {
            "id": "exampleSDID@32473",
            "params": [["iut", "3"], ["eventSource", "Application"], ["eventID", "1011"]],
        },
        {"id": "examplePriority@32473", "params": [["class", "high"]]},
    ]
    assert parsed.message == "An application event log entry..."


def test_parse_nilvalue_fields():
    parsed = parse_rfc5424("<13>1 - - - - - -")
    assert parsed.timestamp is None
    assert parsed.hostname is None
    assert parsed.app_name is None
    assert parsed.procid is None
    assert parsed.msgid is None
    assert parsed.structured_data == []
    assert parsed.message == ""


def test_parse_timestamp_keeps_source_precision_and_timezone():
    parsed = parse_rfc5424("<13>1 2003-08-24T05:14:15.000003-07:00 host app 1 ID1 - body")
    assert parsed.timestamp == "2003-08-24T05:14:15.000003-07:00"


def test_parse_body_strips_utf8_bom_and_keeps_newlines():
    parsed = parse_rfc5424("<13>1 - host app 1 ID1 - \ufefffirst line\nsecond line")
    assert parsed.message == "first line\nsecond line"


def test_parse_escaped_characters_do_not_truncate_element():
    parsed = parse_rfc5424(
        "<13>1 - host app 1 ID1 "
        "[id a=\"x\\]y\\\\z\\\"w\" b=\"after\"][next c=\"v\"] body"
    )
    assert parsed.structured_data == [
        {"id": "id", "params": [["a", "x]y\\z\"w"], ["b", "after"]]},
        {"id": "next", "params": [["c", "v"]]},
    ]
    assert parsed.message == "body"


def test_parse_non_escape_backslash_is_kept_literally():
    parsed = parse_rfc5424("<13>1 - host app 1 ID1 [id a=\"C:\\temp\"] -")
    assert parsed.structured_data == [{"id": "id", "params": [["a", "C:\\temp"]]}]


def test_parse_duplicate_elements_and_params_are_preserved_in_order():
    parsed = parse_rfc5424(
        "<13>1 - host app 1 ID1 [dup k=\"1\" k=\"2\"][dup k=\"3\"] -"
    )
    assert parsed.structured_data == [
        {"id": "dup", "params": [["k", "1"], ["k", "2"]]},
        {"id": "dup", "params": [["k", "3"]]},
    ]


@pytest.mark.parametrize(
    "message",
    [
        "13>1 - host app 1 ID1 - body",  # missing '<'
        "<999>1 - host app 1 ID1 - body",  # PRI out of range
        "<1234>1 - host app 1 ID1 - body",  # PRI too long
        "<ab>1 - host app 1 ID1 - body",  # PRI not digits
        "<13",  # unterminated PRI
        "<13>0 - host app 1 ID1 - body",  # VERSION must not start with 0
        "<13>1 notatimestamp host app 1 ID1 - body",
        "<13>1 - host app 1 ID1 [unclosed a=\"1\"",  # unterminated SD-ELEMENT
        "<13>1 - host app 1 ID1 [id a=\"truncated\\",  # truncated escape
        "<13>1 - host app 1 ID1 [id a=\"unterminated",  # unterminated PARAM-VALUE
        "<13>1 - host app 1 ID1 [id novalue] -",  # missing '=' and value
    ],
)
def test_parse_invalid_messages_raise(message: str):
    with pytest.raises(SyslogParseError):
        parse_rfc5424(message)


def test_error_reports_byte_position_without_message_content():
    secret = "s3cr3t-token"
    message = f"<13>1 - host app 1 ID1 [id a=\"{secret}\\"
    with pytest.raises(SyslogParseError) as exc_info:
        parse_rfc5424(message)
    error = exc_info.value
    assert isinstance(error.position, int)
    # the truncated escape is at the very end of the message
    assert error.position == len(message.encode("utf-8")) - 1
    assert secret not in str(error)
    assert message not in str(error)


def test_error_byte_position_accounts_for_multibyte_characters():
    # 'é' is 2 bytes in UTF-8, so the byte offset of the truncated escape
    # differs from its character offset
    message = "<13>1 - host app 1 ID1 [id a=\"é\\"
    with pytest.raises(SyslogParseError) as exc_info:
        parse_rfc5424(message)
    assert exc_info.value.position == len(message.encode("utf-8")) - 1
    assert exc_info.value.position != len(message) - 1


### FORMATTER TESTS
### ============================================================================
def test_formatter_full_json_output(env: LoggingEnvironment):
    env.handler.setFormatter(SyslogFormatter("%(message)s"))
    env.logger.info(
        "<13>1 2003-10-11T22:14:15.003Z mymachine su 123 ID47 "
        "[exampleSDID@32473 iut=\"3\"][examplePriority@32473 class=\"high\"]"
        " \ufefffirst line\nsecond line"
    )
    log_json = env.load_json()
    assert log_json == {
        "message": (
            "<13>1 2003-10-11T22:14:15.003Z mymachine su 123 ID47 "
            "[exampleSDID@32473 iut=\"3\"][examplePriority@32473 class=\"high\"]"
            " \ufefffirst line\nsecond line"
        ),
        "syslog": {
            "priority": 13,
            "facility": 1,
            "severity": 5,
            "version": 1,
            "timestamp": "2003-10-11T22:14:15.003Z",
            "hostname": "mymachine",
            "app_name": "su",
            "procid": "123",
            "msgid": "ID47",
            "structured_data": [
                {"id": "exampleSDID@32473", "params": [["iut", "3"]]},
                {"id": "examplePriority@32473", "params": [["class", "high"]]},
            ],
            "message": "first line\nsecond line",
        },
    }


def test_formatter_source_metadata_does_not_override_record(env: LoggingEnvironment):
    env.handler.setFormatter(SyslogFormatter("%(levelname)s %(name)s %(message)s"))
    env.logger.warning("<2>1 - remote-host app 1 ID1 - body")
    log_json = env.load_json()
    # local trusted metadata is untouched...
    assert log_json["levelname"] == "WARNING"
    assert log_json["name"] == env.logger.name
    # ...whilst source claims live in the syslog object
    assert log_json["syslog"]["severity"] == 2
    assert log_json["syslog"]["hostname"] == "remote-host"
    assert "hostname" not in log_json


def test_formatter_strict_strategy_raises():
    formatter = SyslogFormatter("%(message)s", on_parse_error="strict")
    with pytest.raises(SyslogParseError):
        formatter.format(make_record("not a syslog message"))


def test_formatter_preserve_strategy_keeps_original_message(env: LoggingEnvironment):
    env.handler.setFormatter(SyslogFormatter("%(message)s", on_parse_error="preserve"))
    env.logger.info("not a syslog message")
    log_json = env.load_json()
    assert log_json == {"message": "not a syslog message"}


def test_formatter_default_strategy_is_strict():
    formatter = SyslogFormatter("%(message)s")
    assert formatter.on_parse_error == "strict"
    with pytest.raises(SyslogParseError):
        formatter.format(make_record("not a syslog message"))


def test_formatter_invalid_strategy_rejected():
    with pytest.raises(ValueError):
        SyslogFormatter("%(message)s", on_parse_error="ignore")


def test_formatter_custom_syslog_field(env: LoggingEnvironment):
    env.handler.setFormatter(SyslogFormatter("%(message)s", syslog_field="rfc5424"))
    env.logger.info("<13>1 - host app 1 ID1 - body")
    log_json = env.load_json()
    assert log_json["rfc5424"]["hostname"] == "host"
    assert "syslog" not in log_json
