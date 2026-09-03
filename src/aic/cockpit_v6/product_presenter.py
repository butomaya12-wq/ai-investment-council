from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


PACKAGE = Path(__file__).resolve().parent

SNAPSHOT_PATH = (
    PACKAGE
    / "data"
    / "product_state_snapshot_v1.json"
)


def build_product_state() -> dict[str, Any]:
    try:
        payload = json.loads(
            SNAPSHOT_PATH.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Market Jury tracked product "
            "snapshot unavailable"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Market Jury product snapshot "
            "must be an object"
        )

    broker = payload.get("broker")

    if not isinstance(broker, dict):
        raise RuntimeError(
            "Market Jury broker projection missing"
        )

    if broker.get("broker_writes") != 0:
        raise RuntimeError(
            "tracked projection broker-write "
            "invariant violated"
        )

    if broker.get("orders") != 0:
        raise RuntimeError(
            "tracked projection order "
            "invariant violated"
        )

    if (
        broker.get("live_money")
        != "PROHIBITED"
    ):
        raise RuntimeError(
            "tracked projection live-money "
            "invariant violated"
        )

    product = payload.get("product")

    if not isinstance(product, dict):
        raise RuntimeError(
            "tracked product metadata missing"
        )

    if (
        product.get("projection_authority")
        is not False
    ):
        raise RuntimeError(
            "tracked display projection "
            "must have no authority"
        )

    return deepcopy(payload)
