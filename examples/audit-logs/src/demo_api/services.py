from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from opentelemetry import trace

from observe_kit.audit import audit
from observe_kit.context import get_request_context

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def build_quote(customer_id: str, items: list[dict[str, Any]], request: Any | None = None) -> dict[str, Any]:
    context = get_request_context()
    logger.info(
        "quote_requested",
        extra={
            "customer_id": customer_id,
            "route": context.route,
            "trace_id": context.trace_id,
            "item_count": len(items),
        },
    )

    with tracer.start_as_current_span("quote_pipeline") as quote_span:
        quote_span.set_attribute("app.customer_id", customer_id)
        quote_span.set_attribute("app.item_count", len(items))

        availability = _check_inventory(items)
        pricing = _price_items(items)

        subtotal = pricing["subtotal"]
        tax = (subtotal * Decimal("0.07")).quantize(Decimal("0.01"))
        total = (subtotal + tax).quantize(Decimal("0.01"))

        quote_span.set_attribute("app.subtotal", float(subtotal))
        quote_span.set_attribute("app.total", float(total))

    logger.info(
        "quote_completed",
        extra={
            "customer_id": customer_id,
            "trace_id": context.trace_id,
            "route": context.route,
            "total": str(total),
            "available": availability["available"],
        },
    )

    audit(
        actor=getattr(request, "user", None) if getattr(getattr(request, "user", None), "is_authenticated", False) else None,
        action="quote_generated",
        obj=None,
        extra={
            "customer_id": customer_id,
            "item_count": len(items),
            "total": f"{total:.2f}",
        },
        request=request,
    )

    return {
        "customer_id": customer_id,
        "currency": "USD",
        "inventory": availability,
        "pricing": {
            "subtotal": f"{subtotal:.2f}",
            "tax": f"{tax:.2f}",
            "total": f"{total:.2f}",
        },
    }


def _check_inventory(items: list[dict[str, Any]]) -> dict[str, Any]:
    with tracer.start_as_current_span("inventory_check") as span:
        sku_count = len({item["sku"] for item in items})
        span.set_attribute("inventory.unique_skus", sku_count)
        logger.info("inventory_checked", extra={"sku_count": sku_count})
        return {"available": True, "checked_skus": sku_count}


def _price_items(items: list[dict[str, Any]]) -> dict[str, Decimal]:
    with tracer.start_as_current_span("price_quote") as span:
        subtotal = sum(
            Decimal(str(item["unit_price"])) * Decimal(item["quantity"]) for item in items
        )
        span.set_attribute("pricing.subtotal", float(subtotal))
        logger.info("pricing_calculated", extra={"subtotal": f"{subtotal:.2f}"})
        return {"subtotal": subtotal}
