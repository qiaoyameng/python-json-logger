from __future__ import annotations

from collections.abc import Generator
import enum
import io
import json
import logging
import threading
import uuid
from typing import Any

import pytest

import pythonjsonlogger
from pythonjsonlogger import AdapterLocation, TypeAdapterRegistry
from pythonjsonlogger.core import BaseJsonFormatter
from pythonjsonlogger.json import JsonFormatter

if pythonjsonlogger.ORJSON_AVAILABLE:
    from pythonjsonlogger.orjson import OrjsonFormatter

if pythonjsonlogger.MSGSPEC_AVAILABLE:
    from pythonjsonlogger.msgspec import MsgspecFormatter


ALL_FORMATTERS: list[type[BaseJsonFormatter]] = [JsonFormatter]
if pythonjsonlogger.ORJSON_AVAILABLE:
    ALL_FORMATTERS.append(OrjsonFormatter)
if pythonjsonlogger.MSGSPEC_AVAILABLE:
    ALL_FORMATTERS.append(MsgspecFormatter)


@pytest.fixture
def env() -> Generator[tuple[logging.Logger, io.StringIO, logging.Handler], None, None]:
    logger = logging.getLogger("pythonjsonlogger.tests.adapters")
    logger.setLevel(logging.DEBUG)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    logger.addHandler(handler)
    yield logger, buffer, handler
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    buffer.close()


class BaseModel:
    pass


class MiddleModel(BaseModel):
    pass


class LeafModel(MiddleModel):
    pass


class LeftModel:
    pass


class RightModel:
    pass


class JoinedModel(LeftModel, RightModel):
    pass


class Currency(enum.Enum):
    USD = "usd"


class Money:
    def __init__(self, amount: int, currency: Currency) -> None:
        self.amount = amount
        self.currency = currency


class SelfReturning:
    pass


class Cyclic:
    pass


class MutualCycleA:
    pass


class MutualCycleB:
    pass


class FiniteChainStart:
    pass


class FiniteChainNext:
    pass


class FiniteChainThird:
    pass


class RepeatedAdapterValue:
    pass


class FallbackOnly:
    pass


def test_exact_adapter_takes_precedence_over_parent():
    registry = TypeAdapterRegistry()
    registry.register(BaseModel, lambda obj, position: "base")
    registry.register(LeafModel, lambda obj, position: "leaf")

    assert registry.snapshot().convert(LeafModel()) == "leaf"


def test_most_specific_parent_adapter_is_selected_independent_of_order():
    def registry_with(order: tuple[type[Any], type[Any]]) -> TypeAdapterRegistry:
        registry = TypeAdapterRegistry()
        for type_ in order:
            name = type_.__name__.lower()
            registry.register(
                type_,
                lambda obj, position, name=name: name,
            )
        return registry

    registry_a = registry_with((MiddleModel, BaseModel))
    registry_b = registry_with((BaseModel, MiddleModel))

    assert registry_a.snapshot().convert(LeafModel()) == "middlemodel"
    assert registry_b.snapshot().convert(LeafModel()) == "middlemodel"


def test_incompatible_multiple_inheritance_is_ambiguous():
    registry_a = TypeAdapterRegistry()
    registry_a.register(LeftModel, lambda obj, position: "left")
    registry_a.register(RightModel, lambda obj, position: "right")

    registry_b = TypeAdapterRegistry()
    registry_b.register(RightModel, lambda obj, position: "right")
    registry_b.register(LeftModel, lambda obj, position: "left")

    with pytest.raises(TypeError, match="Ambiguous type adapters"):
        registry_a.snapshot().convert(JoinedModel())

    with pytest.raises(TypeError, match="Ambiguous type adapters"):
        registry_b.snapshot().convert(JoinedModel())


def test_exact_adapter_resolves_multiple_inheritance():
    registry = TypeAdapterRegistry()
    registry.register(LeftModel, lambda obj, position: "left")
    registry.register(RightModel, lambda obj, position: "right")
    registry.register(JoinedModel, lambda obj, position: "joined")

    assert registry.snapshot().convert(JoinedModel()) == "joined"


def test_snapshot_is_stable_when_registry_changes():
    registry = TypeAdapterRegistry()
    registry.register(BaseModel, lambda obj, position: "old")
    snapshot = registry.snapshot()
    registry.register(BaseModel, lambda obj, position: "new")

    assert snapshot.convert(BaseModel()) == "old"


def test_snapshot_used_during_conversion_is_stable_across_thread_update():
    registry = TypeAdapterRegistry()
    started = threading.Event()
    updated = threading.Event()

    class SnapshotHolder(BaseModel):
        def __init__(self) -> None:
            self.child = LeafModel()

    def old_adapter(obj: Any, position: Any) -> Any:
        if isinstance(obj, SnapshotHolder):
            started.set()
            assert updated.wait(1)
            return {"child": obj.child}
        return "old"

    registry.register(BaseModel, old_adapter)

    def update_registry() -> None:
        assert started.wait(1)
        registry.register(BaseModel, lambda obj, position: "new")
        registry.register(LeafModel, lambda obj, position: "new-leaf")
        updated.set()

    thread = threading.Thread(target=update_registry)
    thread.start()
    try:
        assert registry.snapshot().convert(SnapshotHolder()) == {"child": "old"}
    finally:
        thread.join()
        updated.set()


@pytest.mark.parametrize("formatter_class", ALL_FORMATTERS)
def test_nested_containers_and_position_specific_adapter(
    env: tuple[logging.Logger, io.StringIO, logging.Handler],
    formatter_class: type[BaseJsonFormatter],
):
    logger, buffer, handler = env
    money = Money(100, Currency.USD)
    identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
    positions: list[Any] = []
    registry = TypeAdapterRegistry()

    def adapt_money(obj: Money, position: Any) -> Any:
        positions.append((obj, position.location, position.path))
        if position.location is AdapterLocation.KEY:
            return f"money:{obj.amount}:{obj.currency.value}"
        return {"amount": obj.amount, "currency": obj.currency}

    registry.register(Money, adapt_money)
    registry.register(Currency, lambda obj, position: obj.value)
    registry.register(uuid.UUID, lambda obj, position: str(obj))
    handler.setFormatter(formatter_class(type_adapters=registry))

    logger.info(
        "hello",
        extra={
            "data": {
                "items": (money,),
                money: {"currency": money.currency, "id": identifier},
            },
            "id": identifier,
        },
    )

    result = json.loads(buffer.getvalue())
    assert result["data"]["items"] == [{"amount": 100, "currency": "usd"}]
    assert result["data"]["money:100:usd"] == {"currency": "usd", "id": str(identifier)}
    assert result["id"] == str(identifier)
    assert (money, AdapterLocation.KEY, ("data", money)) in positions
    assert (money, AdapterLocation.VALUE, ("data", "items", 0)) in positions


@pytest.mark.parametrize("formatter_class", ALL_FORMATTERS)
def test_adapter_takes_precedence_over_json_default(
    env: tuple[logging.Logger, io.StringIO, logging.Handler],
    formatter_class: type[BaseJsonFormatter],
):
    logger, buffer, handler = env
    registry = TypeAdapterRegistry()
    registry.register(Money, lambda obj, position: {"adapter": True})

    def json_default(obj: Any) -> Any:
        if isinstance(obj, Money):
            raise AssertionError("json_default must not be used for registered types")
        if isinstance(obj, FallbackOnly):
            return {"fallback": True}
        raise TypeError(f"Unsupported type: {type(obj)!r}")

    handler.setFormatter(
        formatter_class(  # type: ignore[call-arg]
            type_adapters=registry, json_default=json_default
        )
    )
    logger.info("hello", extra={"money": Money(1, Currency.USD), "other": FallbackOnly()})

    result = json.loads(buffer.getvalue())
    assert result["money"] == {"adapter": True}
    assert result["other"] == {"fallback": True}


def test_adapter_returning_original_object_fails():
    registry = TypeAdapterRegistry()
    registry.register(SelfReturning, lambda obj, position: obj)

    with pytest.raises(TypeError, match="returned the original object"):
        registry.snapshot().convert(SelfReturning())


def test_adapter_result_that_forms_a_cycle_fails():
    registry = TypeAdapterRegistry()
    registry.register(Cyclic, lambda obj, position: {"back": obj})

    with pytest.raises(TypeError, match="Circular reference"):
        registry.snapshot().convert(Cyclic())


def test_mutual_adapter_cycle_fails():
    registry = TypeAdapterRegistry()
    registry.register(MutualCycleA, lambda obj, position: MutualCycleB())
    registry.register(MutualCycleB, lambda obj, position: MutualCycleA())

    with pytest.raises(TypeError, match="Circular reference"):
        registry.snapshot().convert(MutualCycleA())


def test_finite_adapter_chain_with_same_adapter_is_allowed():
    registry = TypeAdapterRegistry()

    def chain_adapter(obj: Any, position: Any) -> Any:
        if isinstance(obj, FiniteChainStart):
            return FiniteChainNext()
        return "final"

    registry.register(FiniteChainStart, chain_adapter)
    registry.register(FiniteChainNext, chain_adapter)

    assert registry.snapshot().convert(FiniteChainStart()) == "final"


def test_finite_adapter_chain_through_multiple_types_is_allowed():
    registry = TypeAdapterRegistry()

    def chain_adapter(obj: Any, position: Any) -> Any:
        if isinstance(obj, FiniteChainStart):
            return FiniteChainNext()
        if isinstance(obj, FiniteChainNext):
            return FiniteChainThird()
        return "final"

    registry.register(FiniteChainStart, chain_adapter)
    registry.register(FiniteChainNext, chain_adapter)
    registry.register(FiniteChainThird, chain_adapter)

    assert registry.snapshot().convert(FiniteChainStart()) == "final"


def test_repeated_type_adapter_chain_fails():
    registry = TypeAdapterRegistry()

    def repeat_adapter(obj: RepeatedAdapterValue, position: Any) -> Any:
        return RepeatedAdapterValue()

    registry.register(RepeatedAdapterValue, repeat_adapter)

    with pytest.raises(TypeError, match="Circular reference"):
        registry.snapshot().convert(RepeatedAdapterValue())


def test_native_circular_container_fails():
    registry = TypeAdapterRegistry()
    cycle: dict[str, Any] = {}
    cycle["self"] = cycle

    with pytest.raises(TypeError, match="Circular reference"):
        registry.snapshot().convert(cycle)


@pytest.mark.parametrize("formatter_class", ALL_FORMATTERS)
def test_failed_conversion_does_not_modify_record_object(
    env: tuple[logging.Logger, io.StringIO, logging.Handler],
    formatter_class: type[BaseJsonFormatter],
):
    _, _, handler = env
    original = SelfReturning()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg={"message": "hello", "data": original},
        args=(),
        exc_info=None,
    )
    registry = TypeAdapterRegistry()
    registry.register(SelfReturning, lambda obj, position: obj)
    formatter = formatter_class(type_adapters=registry)
    handler.setFormatter(formatter)

    with pytest.raises(TypeError, match="returned the original object"):
        formatter.format(record)

    assert record.msg == {"message": "hello", "data": original}
    assert record.msg["data"] is original
