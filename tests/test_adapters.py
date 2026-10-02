### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import enum
import io
import json
import logging
import threading
import uuid

## Installed
import pytest

## Application
from pythonjsonlogger.adapters import (
    AdapterLocation,
    TypeAdapterRegistry,
    convert_with_adapters,
)
from pythonjsonlogger.exception import AdapterCycleError, AmbiguousAdapterError
from pythonjsonlogger.json import JsonFormatter


### SETUP
### ============================================================================
class Money:
    def __init__(self, amount: int, currency: str = "USD") -> None:
        self.amount = amount
        self.currency = currency
        return


def money_adapter(obj: Money, location: AdapterLocation) -> str:
    return f"{obj.amount} {obj.currency}"


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "a message", (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def format_record(formatter: JsonFormatter, **extra):
    return json.loads(formatter.format(make_record(**extra)))


### REGISTRY UNIT TESTS
### ============================================================================
class TestRegistry:
    def test_register_validation(self) -> None:
        registry = TypeAdapterRegistry()
        with pytest.raises(TypeError):
            registry.register("not-a-type", money_adapter)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            registry.register(Money, "not-callable")  # type: ignore[arg-type]

    def test_register_unregister(self) -> None:
        registry = TypeAdapterRegistry()
        registry.register(Money, money_adapter)
        assert Money in registry
        assert len(registry) == 1
        registry.unregister(Money)
        assert Money not in registry
        # unregistering a missing type is a no-op
        registry.unregister(Money)

    def test_init_from_mapping(self) -> None:
        registry = TypeAdapterRegistry({Money: money_adapter})
        assert registry.get_adapter(Money) is money_adapter

    def test_snapshot_is_independent(self) -> None:
        registry = TypeAdapterRegistry({Money: money_adapter})
        snapshot = registry.snapshot()
        registry.register(uuid.UUID, lambda obj, location: str(obj))
        registry.unregister(Money)
        assert uuid.UUID not in snapshot
        assert snapshot.get_adapter(Money) is money_adapter


### DISPATCH TESTS
### ============================================================================
class TestDispatch:
    def test_exact_type_wins(self) -> None:
        class SpecialMoney(Money):
            pass

        registry = TypeAdapterRegistry(
            {
                Money: lambda obj, location: "money",
                SpecialMoney: lambda obj, location: "special",
            }
        )
        assert registry.get_adapter(SpecialMoney)(SpecialMoney(1), AdapterLocation.VALUE) == "special"
        assert registry.get_adapter(Money)(Money(1), AdapterLocation.VALUE) == "money"

    def test_parent_adapter_applies_to_child(self) -> None:
        class ChildMoney(Money):
            pass

        registry = TypeAdapterRegistry({Money: money_adapter})
        adapter = registry.get_adapter(ChildMoney)
        assert adapter is money_adapter

    def test_most_specific_adapter_wins(self) -> None:
        class Middle(Money):
            pass

        class Leaf(Middle):
            pass

        registry = TypeAdapterRegistry(
            {
                Money: lambda obj, location: "money",
                Middle: lambda obj, location: "middle",
            }
        )
        assert registry.get_adapter(Leaf)(Leaf(1), AdapterLocation.VALUE) == "middle"

    def test_ambiguous_multiple_inheritance(self) -> None:
        class Serializable:
            pass

        class Hybrid(Money, Serializable):
            pass

        registry = TypeAdapterRegistry(
            {
                Money: money_adapter,
                Serializable: lambda obj, location: "serializable",
            }
        )
        with pytest.raises(AmbiguousAdapterError) as exc_info:
            registry.get_adapter(Hybrid)
        assert exc_info.value.cls is Hybrid
        assert set(exc_info.value.candidates) == {Money, Serializable}

    def test_diamond_inheritance_is_not_ambiguous(self) -> None:
        class Base:
            pass

        class Left(Base):
            pass

        class Diamond(Left):
            pass

        registry = TypeAdapterRegistry(
            {
                Base: lambda obj, location: "base",
                Left: lambda obj, location: "left",
            }
        )
        assert registry.get_adapter(Diamond)(Diamond(), AdapterLocation.VALUE) == "left"

    def test_no_adapter_returns_none(self) -> None:
        registry = TypeAdapterRegistry({Money: money_adapter})
        assert registry.get_adapter(uuid.UUID) is None


### CONVERSION TESTS
### ============================================================================
class TestConversion:
    def test_nested_containers(self) -> None:
        uid = uuid.uuid4()
        registry = TypeAdapterRegistry({uuid.UUID: lambda obj, location: str(obj)})
        data = {
            "list": [uid],
            "tuple": (uid, 1),
            "set": {uid},
            "nested": {"deep": [{"money": Money(5)}]},
        }
        registry.register(Money, money_adapter)
        result = convert_with_adapters(data, registry)
        assert result == {
            "list": [str(uid)],
            "tuple": (str(uid), 1),
            "set": {str(uid)},
            "nested": {"deep": [{"money": "5 USD"}]},
        }
        # input is not mutated
        assert data["list"] == [uid]
        assert isinstance(data["nested"]["deep"][0]["money"], Money)

    def test_adapter_result_is_recursively_converted(self) -> None:
        uid = uuid.uuid4()
        registry = TypeAdapterRegistry(
            {
                Money: lambda obj, location: {"id": uid, "amount": obj.amount},
                uuid.UUID: lambda obj, location: str(obj),
            }
        )
        result = convert_with_adapters(Money(3), registry)
        assert result == {"id": str(uid), "amount": 3}

    def test_adapter_receives_location(self) -> None:
        locations = []

        def adapter(obj: uuid.UUID, location: AdapterLocation) -> str:
            locations.append(location)
            return str(obj)

        uid = uuid.uuid4()
        registry = TypeAdapterRegistry({uuid.UUID: adapter})
        convert_with_adapters({uid: uid}, registry)
        assert locations == [AdapterLocation.KEY, AdapterLocation.VALUE]

    def test_key_and_value_rules_can_differ(self) -> None:
        def adapter(obj: uuid.UUID, location: AdapterLocation) -> str:
            if location is AdapterLocation.KEY:
                return obj.hex
            return str(obj)

        uid = uuid.uuid4()
        registry = TypeAdapterRegistry({uuid.UUID: adapter})
        result = convert_with_adapters({uid: uid}, registry)
        assert result == {uid.hex: str(uid)}

    def test_self_returning_adapter_raises(self) -> None:
        registry = TypeAdapterRegistry({Money: lambda obj, location: obj})
        with pytest.raises(AdapterCycleError, match="returned the object it was given"):
            convert_with_adapters(Money(1), registry)

    def test_ping_pong_cycle_raises(self) -> None:
        class BoxA:
            def __init__(self) -> None:
                self.other: BoxB | None = None

        class BoxB:
            def __init__(self, a: BoxA) -> None:
                self.a = a

        a = BoxA()
        b = BoxB(a)
        a.other = b
        registry = TypeAdapterRegistry(
            {
                BoxA: lambda obj, location: obj.other,
                BoxB: lambda obj, location: obj.a,
            }
        )
        with pytest.raises(AdapterCycleError):
            convert_with_adapters(a, registry)

    def test_container_cycle_raises(self) -> None:
        registry = TypeAdapterRegistry()
        data: list = []
        data.append(data)
        with pytest.raises(AdapterCycleError):
            convert_with_adapters(data, registry)

    def test_unregistered_type_passes_through(self) -> None:
        registry = TypeAdapterRegistry()
        money = Money(1)
        assert convert_with_adapters(money, registry) is money

    def test_primitives_pass_through(self) -> None:
        registry = TypeAdapterRegistry({Money: money_adapter})
        assert convert_with_adapters({"a": [1, 2.5, True, None, "x"]}, registry) == {
            "a": [1, 2.5, True, None, "x"]
        }


### FORMATTER INTEGRATION TESTS
### ============================================================================
class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class TestFormatter:
    def test_formatter_converts_registered_types(self) -> None:
        uid = uuid.uuid4()
        formatter = JsonFormatter(
            type_adapters={
                Money: money_adapter,
                uuid.UUID: lambda obj, location: str(obj),
                Color: lambda obj, location: obj.value,
            }
        )
        output = format_record(formatter, money=Money(10), request_id=uid, color=Color.RED)
        assert output["money"] == "10 USD"
        assert output["request_id"] == str(uid)
        assert output["color"] == "red"

    def test_formatter_accepts_registry_instance(self) -> None:
        registry = TypeAdapterRegistry({Money: money_adapter})
        formatter = JsonFormatter(type_adapters=registry)
        assert formatter.type_adapters is registry
        assert format_record(formatter, money=Money(2))["money"] == "2 USD"

    def test_non_string_keys(self) -> None:
        uid = uuid.uuid4()
        formatter = JsonFormatter(type_adapters={uuid.UUID: lambda obj, location: str(obj)})
        output = format_record(formatter, mapping={uid: Money(7)})
        # no Money adapter registered: value falls back to the encoder default
        assert list(output["mapping"]) == [str(uid)]

    def test_non_string_keys_with_key_rules(self) -> None:
        uid = uuid.uuid4()

        def adapter(obj: uuid.UUID, location: AdapterLocation) -> str:
            return obj.hex if location is AdapterLocation.KEY else str(obj)

        formatter = JsonFormatter(type_adapters={uuid.UUID: adapter})
        output = format_record(formatter, mapping={uid: uid})
        assert output["mapping"] == {uid.hex: str(uid)}

    def test_registry_takes_precedence_over_json_default(self) -> None:
        uid = uuid.uuid4()
        formatter = JsonFormatter(
            type_adapters={uuid.UUID: lambda obj, location: "from-registry"},
            json_default=lambda obj: "from-default",
        )
        output = format_record(formatter, request_id=uid, money=Money(1))
        assert output["request_id"] == "from-registry"
        # unregistered types still fall back to json_default
        assert output["money"] == "from-default"

    def test_json_default_fallback_without_registry(self) -> None:
        formatter = JsonFormatter(json_default=lambda obj: "fallback")
        output = format_record(formatter, money=Money(1))
        assert output["money"] == "fallback"

    def test_registry_updates_apply_to_next_record(self) -> None:
        registry = TypeAdapterRegistry({Money: lambda obj, location: "v1"})
        formatter = JsonFormatter(type_adapters=registry)
        assert format_record(formatter, money=Money(1))["money"] == "v1"
        registry.register(Money, lambda obj, location: "v2")
        assert format_record(formatter, money=Money(1))["money"] == "v2"

    def test_snapshot_isolation_within_single_format(self) -> None:
        """Registering an adapter mid-format must not affect the in-flight record."""
        uid = uuid.uuid4()
        registry = TypeAdapterRegistry()

        def trigger_adapter(obj: Money, location: AdapterLocation) -> str:
            # simulates another thread updating the registry mid-format
            registry.register(uuid.UUID, lambda obj, location: "late-adapter")
            return "triggered"

        registry.register(Money, trigger_adapter)
        formatter = JsonFormatter(type_adapters=registry)

        record = make_record()
        record.trigger = Money(1)  # converted first, registers the UUID adapter
        record.request_id = uid
        output = json.loads(formatter.format(record))

        assert output["trigger"] == "triggered"
        # the in-flight record still uses the old snapshot: no registered UUID
        # adapter, so the encoder default (str) applies
        assert output["request_id"] == str(uid)
        # the next record sees the updated registry
        assert format_record(formatter, request_id=uid)["request_id"] == "late-adapter"

    def test_concurrent_updates_never_mix_rules(self) -> None:
        registry = TypeAdapterRegistry({Money: lambda obj, location: "v1"})
        formatter = JsonFormatter(type_adapters=registry)
        errors: list[Exception] = []
        stop = threading.Event()

        def writer() -> None:
            version = 1
            while not stop.is_set():
                version += 1
                registry.register(Money, lambda obj, location, v=version: f"v{v}")

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(200):
                record = make_record()
                record.first = Money(1)
                record.second = Money(2)
                output = json.loads(formatter.format(record))
                if output["first"] != output["second"]:
                    errors.append(AssertionError(f"mixed rules: {output}"))
        finally:
            stop.set()
            thread.join()
        assert not errors

    def test_ambiguity_error_surfaces_on_format(self) -> None:
        class Serializable:
            pass

        class Hybrid(Money, Serializable):
            pass

        formatter = JsonFormatter(
            type_adapters={
                Money: money_adapter,
                Serializable: lambda obj, location: "serializable",
            }
        )
        with pytest.raises(AmbiguousAdapterError):
            formatter.format(make_record(hybrid=Hybrid(1)))

    def test_cycle_error_surfaces_on_format(self) -> None:
        formatter = JsonFormatter(type_adapters={Money: lambda obj, location: obj})
        with pytest.raises(AdapterCycleError):
            formatter.format(make_record(money=Money(1)))

    def test_failure_does_not_mutate_record(self) -> None:
        def bad_adapter(obj: Money, location: AdapterLocation) -> str:
            raise RuntimeError("boom")

        uid = uuid.uuid4()
        payload = {"id": uid, "items": [uid]}
        formatter = JsonFormatter(
            type_adapters={
                uuid.UUID: lambda obj, location: str(obj),
                Money: bad_adapter,
            }
        )
        record = make_record(payload=payload, money=Money(1))
        with pytest.raises(RuntimeError, match="boom"):
            formatter.format(record)
        # original objects on the record are untouched
        assert record.payload is payload
        assert record.payload == {"id": uid, "items": [uid]}
        assert isinstance(record.money, Money)

    def test_successful_conversion_does_not_mutate_record(self) -> None:
        uid = uuid.uuid4()
        payload = {"id": uid, "items": [uid]}
        formatter = JsonFormatter(type_adapters={uuid.UUID: lambda obj, location: str(obj)})
        record = make_record(payload=payload)
        output = json.loads(formatter.format(record))
        assert output["payload"] == {"id": str(uid), "items": [str(uid)]}
        # record still holds the original, unconverted objects
        assert record.payload is payload
        assert record.payload == {"id": uid, "items": [uid]}
