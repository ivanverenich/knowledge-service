from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, Field

from knowledge_service.contracts import Usage


class ModelPrice(BaseModel):
    price_id: str = Field(min_length=1)
    input_usd_per_million_tokens: Decimal = Field(ge=0)
    output_usd_per_million_tokens: Decimal = Field(ge=0)


def estimate_cost(
    usage: Usage,
    prices: Mapping[str, ModelPrice],
) -> Decimal | None:
    if usage.price_id is None:
        return None

    price = prices.get(usage.price_id)
    if price is None:
        return None

    million = Decimal("1000000")
    input_cost = (
        Decimal(usage.input_tokens) * price.input_usd_per_million_tokens / million
    )
    output_cost = (
        Decimal(usage.output_tokens) * price.output_usd_per_million_tokens / million
    )
    return input_cost + output_cost
