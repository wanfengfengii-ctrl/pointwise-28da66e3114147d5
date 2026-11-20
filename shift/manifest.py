from __future__ import annotations

from dataclasses import dataclass

from archive.results import find_signed_result
from capture.orders import is_cancelled, list_orders_for_shift
from device.pulses import PulseIdentity
from shift.reconcile import classify_order
from storage import transaction


@dataclass(frozen=True)
class ManifestEntry:
    order_id: str
    pulse: PulseIdentity
    profile_key: tuple[str, str]
    status: str
    receipt_id: str | None
    peak_kpa: int | None


def manifest(connection, shift):
    with transaction(connection):
        entries = []
        for order in list_orders_for_shift(connection, shift):
            signed = find_signed_result(connection, order.order_id)
            cancelled = is_cancelled(connection, order.order_id)
            entries.append(ManifestEntry(
                order.order_id, order.pulse, order.profile_key, classify_order(order, signed, cancelled),
                None if signed is None else signed.receipt.receipt_id,
                None if signed is None else signed.measurement.peak_kpa
            ))
        return tuple(entries)
