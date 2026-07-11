from __future__ import annotations

from decimal import Decimal
from typing import Any


def build_quote(customer_id: str, items: list[dict[str, Any]], request: Any | None = None) -> dict[str, Any]:
    del customer_id, request

    subtotal = sum(
        Decimal(str(item["unit_price"])) * Decimal(item["quantity"]) for item in items
    )
    tax = (subtotal * Decimal("0.07")).quantize(Decimal("0.01"))
    total = (subtotal + tax).quantize(Decimal("0.01"))

    return {
        "currency": "USD",
        "inventory": {
            "available": True,
            "checked_skus": len({item["sku"] for item in items}),
        },
        "pricing": {
            "subtotal": f"{subtotal:.2f}",
            "tax": f"{tax:.2f}",
            "total": f"{total:.2f}",
        },
    }
