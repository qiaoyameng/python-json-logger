"""Tests for the `max_bytes` budget enforcement of JSON formatters"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import functools
import json
import logging
import sys
from typing import Any

## Installed
import pytest

## Application
from pythonjsonlogger.core import TRUNCATED_DICT_MARKER_KEY, TRUNCATED_VALUE_MARKER
from pythonjsonlogger.exception import MaxBytesExceededError
from pythonjsonlogger.json import JsonFormatter

### HELPERS
### ============================================================================


def make_record(
    msg: Any = "test message",
    level: int = logging.INFO,
    exc_info: Any = None,
    **extra: Any,
) -> logging.LogRecord:
    record = logging.LogRecord("test", level, __file__, 10, msg, None, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def assert_valid_trimmed_output(output: str, max_bytes: int) -> dict:
    """Assert the output fits the budget and is parseable JSON, return the parsed data"""
    assert utf8_len(output) <= max_bytes
    return json.loads(output)


### TESTS
### ============================================================================
def test_max_bytes_must_be_positive():
    with pytest.raises(ValueError, match="max_bytes"):
        JsonFormatter(max_bytes=0)


def test_no_trim_when_within_budget():
    record = make_record("hello")
    expected = JsonFormatter(fmt=["levelname", "message"]).format(record)
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=16 * 1024)
    output = formatter.format(record)
    assert output == expected
    assert "_truncated" not in output


def test_exact_limit_is_not_trimmed():
    record = make_record("刚好等于上限")
    expected = JsonFormatter(fmt=["levelname", "message"], json_ensure_ascii=False).format(record)
    formatter = JsonFormatter(
        fmt=["levelname", "message"],
        json_ensure_ascii=False,
        max_bytes=utf8_len(expected),
    )
    assert formatter.format(record) == expected


def test_chinese_message_truncated_ensure_ascii():
    message = "你好，世界" * 500
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=1024)
    parsed = assert_valid_trimmed_output(formatter.format(make_record(message)), 1024)
    assert parsed["levelname"] == "INFO"
    assert parsed["message"].endswith(TRUNCATED_VALUE_MARKER)
    assert parsed["message"].startswith("你")
    assert parsed["_truncated"]["truncated_fields"] == ["message"]


def test_chinese_message_truncated_no_ascii():
    message = "你好，世界" * 500
    formatter = JsonFormatter(
        fmt=["levelname", "message"], max_bytes=1024, json_ensure_ascii=False
    )
    parsed = assert_valid_trimmed_output(formatter.format(make_record(message)), 1024)
    assert parsed["message"].endswith(TRUNCATED_VALUE_MARKER)
    # without ensure_ascii each Chinese character costs 3 UTF-8 bytes
    kept = parsed["message"][: -len(TRUNCATED_VALUE_MARKER)]
    assert message.startswith(kept)
    assert 0 < len(kept) < len(message)


def test_emoji_message_truncated():
    message = "🔥🎉" * 500
    formatter = JsonFormatter(
        fmt=["levelname", "message"], max_bytes=512, json_ensure_ascii=False
    )
    output = formatter.format(make_record(message))
    parsed = assert_valid_trimmed_output(output, 512)
    assert parsed["message"].endswith(TRUNCATED_VALUE_MARKER)
    # output must be valid UTF-8 - no emoji split mid-sequence by a hard cut
    output.encode("utf-8").decode("utf-8")


def test_quotes_and_backslashes_truncated():
    message = '"quoted" \\path\\to\\"file"\n' * 200
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=512)
    parsed = assert_valid_trimmed_output(formatter.format(make_record(message)), 512)
    kept = parsed["message"][: -len(TRUNCATED_VALUE_MARKER)]
    assert message.startswith(kept)


def test_large_array_truncated_with_marker():
    record = make_record("array", big_list=list(range(10000)))
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=1024)
    parsed = assert_valid_trimmed_output(formatter.format(record), 1024)
    trimmed = parsed["big_list"]
    assert len(trimmed) < 10000
    assert trimmed[:-1] == list(range(len(trimmed) - 1))
    marker = trimmed[-1]
    assert isinstance(marker, str)
    assert "truncated" in marker
    assert str(10000 - (len(trimmed) - 1)) in marker


def test_dict_value_truncated_with_marker():
    payload = {f"key_{i}": f"value_{i}" for i in range(500)}
    record = make_record("dict", payload=payload)
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=1024)
    parsed = assert_valid_trimmed_output(formatter.format(record), 1024)
    trimmed = parsed["payload"]
    assert TRUNCATED_DICT_MARKER_KEY in trimmed
    kept = {key: value for key, value in trimmed.items() if key != TRUNCATED_DICT_MARKER_KEY}
    assert trimmed[TRUNCATED_DICT_MARKER_KEY] == 500 - len(kept)
    assert all(payload[key] == value for key, value in kept.items())


def test_long_exception_text_truncated():
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=768)
    try:
        raise ValueError("boom " + "x" * 5000)
    except ValueError:
        record = make_record("failed", level=logging.ERROR, exc_info=sys.exc_info())
    parsed = assert_valid_trimmed_output(formatter.format(record), 768)
    assert parsed["levelname"] == "ERROR"
    assert parsed["message"] == "failed"
    exc_text = parsed["exc_info"]
    if isinstance(exc_text, str):
        assert exc_text.endswith(TRUNCATED_VALUE_MARKER)
    else:
        pytest.fail("exc_info unexpectedly dropped before truncation was tried")


def test_required_fields_are_preserved():
    message = "z" * 5000
    formatter = JsonFormatter(
        fmt=["asctime", "levelname", "message"],
        max_bytes=512,
        max_bytes_required_fields=["asctime", "levelname"],
    )
    parsed = assert_valid_trimmed_output(formatter.format(make_record(message)), 512)
    assert parsed["asctime"]
    assert parsed["levelname"] == "INFO"
    assert parsed["message"].endswith(TRUNCATED_VALUE_MARKER)


def test_default_required_fields_keep_time_and_level():
    message = "z" * 5000
    formatter = JsonFormatter(fmt=["asctime", "levelname", "message"], max_bytes=512)
    parsed = assert_valid_trimmed_output(formatter.format(make_record(message)), 512)
    assert "asctime" in parsed
    assert parsed["levelname"] == "INFO"


def test_field_priority_order():
    record = make_record("m", field_a="a" * 800, field_b="b" * 800)
    formatter = JsonFormatter(
        fmt=["levelname", "message"],
        max_bytes=1100,
        max_bytes_required_fields=[],
        max_bytes_field_priority=["field_a", "field_b"],
    )
    parsed = assert_valid_trimmed_output(formatter.format(record), 1100)
    # field_a is trimmed first, field_b stays intact
    assert parsed["field_a"].endswith(TRUNCATED_VALUE_MARKER)
    assert parsed["field_b"] == "b" * 800

    reverse = JsonFormatter(
        fmt=["levelname", "message"],
        max_bytes=1100,
        max_bytes_required_fields=[],
        max_bytes_field_priority=["field_b", "field_a"],
    )
    parsed = assert_valid_trimmed_output(reverse.format(record), 1100)
    assert parsed["field_b"].endswith(TRUNCATED_VALUE_MARKER)
    assert parsed["field_a"] == "a" * 800


def test_fields_dropped_when_truncation_is_not_enough():
    record = make_record("m", extra_field="x" * 5000)
    formatter = JsonFormatter(
        fmt=["levelname", "message"],
        max_bytes=130,
        max_bytes_required_fields=["levelname", "message"],
    )
    parsed = assert_valid_trimmed_output(formatter.format(record), 200)
    assert parsed["levelname"] == "INFO"
    assert parsed["message"] == "m"
    assert "extra_field" not in parsed
    assert parsed["_truncated"]["dropped_fields"] == ["extra_field"]


def test_minimal_record_too_big_raises():
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=10)
    with pytest.raises(MaxBytesExceededError) as exc_info:
        formatter.format(make_record("this will never fit"))
    assert exc_info.value.max_bytes == 10
    assert exc_info.value.required_bytes > 10


def test_report_counts_against_budget():
    # the trimming report itself consumes budget: the final output including the
    # report must still fit, and the algorithm must terminate
    record = make_record("m", field_a="a" * 500, field_b="b" * 500)
    formatter = JsonFormatter(
        fmt=["levelname", "message"], max_bytes=256, max_bytes_required_fields=[]
    )
    parsed = assert_valid_trimmed_output(formatter.format(record), 256)
    assert "_truncated" in parsed


def test_custom_separators_and_serializer():
    serializer = functools.partial(json.dumps, separators=(",", ":"))
    message = "你好" * 500
    formatter = JsonFormatter(
        fmt=["levelname", "message"],
        json_serializer=serializer,
        json_ensure_ascii=False,
        max_bytes=512,
    )
    output = formatter.format(make_record(message))
    parsed = assert_valid_trimmed_output(output, 512)
    assert ", " not in output
    assert parsed["message"].endswith(TRUNCATED_VALUE_MARKER)


def test_prefix_counts_against_budget():
    record = make_record("x" * 5000)
    formatter = JsonFormatter(fmt=["levelname", "message"], max_bytes=512, prefix="log:")
    output = formatter.format(record)
    assert output.startswith("log:")
    assert_valid_trimmed_output(output[len("log:"):], 512 - len("log:"))
    assert utf8_len(output) <= 512


def test_shared_log_record_is_not_modified():
    shared_list = list(range(1000))
    shared_dict = {f"key_{i}": "v" * 20 for i in range(100)}
    msg_dict = {"nested": "y" * 5000, "other": shared_list}
    record = make_record(msg_dict, big_list=shared_list, big_dict=shared_dict)

    formatter = JsonFormatter(fmt=["levelname"], max_bytes=256)
    formatter.format(record)

    assert shared_list == list(range(1000))
    assert shared_dict == {f"key_{i}": "v" * 20 for i in range(100)}
    assert msg_dict == {"nested": "y" * 5000, "other": shared_list}
    assert record.big_list is shared_list
    assert record.big_dict is shared_dict
    assert record.msg is msg_dict


def test_no_required_fields_everything_can_be_dropped():
    record = make_record("m", field_a="a" * 5000)
    formatter = JsonFormatter(fmt=["message"], max_bytes=110, max_bytes_required_fields=[])
    parsed = assert_valid_trimmed_output(formatter.format(record), 128)
    assert set(parsed["_truncated"]["dropped_fields"]) == {"message", "field_a"}
