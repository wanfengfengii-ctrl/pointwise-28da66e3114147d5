from __future__ import annotations

from dataclasses import dataclass

from archive.results import SignedResult, find_signed_result
from capture.orders import get_order
from shift.provenance import ProvenanceRow, build_provenance_view
from shift.reconcile import Reconciliation, classify_order, reconcile_signed_result
from storage import transaction


@dataclass(frozen=True)
class LookupResult:
    order_id: str
    status: str
    signed: SignedResult | None = None
    provenance: tuple[ProvenanceRow, ...] = ()
    reconciliation: Reconciliation | None = None


def lookup(connection, order_id):
    with transaction(connection):
        order = get_order(connection, order_id)
        signed = find_signed_result(connection, order_id) if order is not None else None
        status = classify_order(order, signed)
        if signed is None:
            return LookupResult(order_id, status)
        return LookupResult(
            order_id, status, signed, build_provenance_view(signed), reconcile_signed_result(order, signed)
        )
