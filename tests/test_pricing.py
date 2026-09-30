from collections.abc import Mapping
from decimal import Decimal

from knowledge_service.contracts import Usage
from knowledge_service.pricing import (
    ModelPrice,
    estimate_cost,
)


def test_pricing_returns_correct_estimation() -> None:
    usage = Usage(
        provider="openai",
        model="gpt4o-mini",
        price_id="standard",
        latency_ms=50,
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )
    model_price = ModelPrice(
        price_id="standard",
        input_usd_per_million_tokens=Decimal("2"),
        output_usd_per_million_tokens=Decimal("8"),
    )
    mapping: Mapping[str, ModelPrice] = {"standard": model_price}
    cost = estimate_cost(usage, mapping)

    assert cost == Decimal("0.006")


def test_usage_without_price_id_estimate_returns_none() -> None:
    usage = Usage(
        provider="openai",
        model="gpt4o-mini",
        latency_ms=50,
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )
    model_price = ModelPrice(
        price_id="standard",
        input_usd_per_million_tokens=Decimal("2"),
        output_usd_per_million_tokens=Decimal("8"),
    )
    mapping: Mapping[str, ModelPrice] = {"standard": model_price}
    cost = estimate_cost(usage, mapping)

    assert cost is None


def test_missed_price_id_estimate_returns_none() -> None:
    usage = Usage(
        provider="openai",
        model="gpt4o-mini",
        price_id="standard",
        latency_ms=50,
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )
    model_price = ModelPrice(
        price_id="pro",
        input_usd_per_million_tokens=Decimal("2"),
        output_usd_per_million_tokens=Decimal("8"),
    )
    mapping: Mapping[str, ModelPrice] = {"pro": model_price}
    cost = estimate_cost(usage, mapping)

    assert cost is None
