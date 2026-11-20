from dataclasses import dataclass

from shift.provenance import build_provenance_view


@dataclass(frozen=True)
class Reconciliation:
    issues: tuple[str, ...]

    @property
    def consistent(self):
        return not self.issues


def classify_order(order, signed, cancelled=False):
    if order is None:
        return 'unknown'
    if signed is not None:
        return 'signed'
    return 'cancelled' if cancelled else 'pending'


def reconcile_signed_result(order, signed):
    issues = []
    if signed.order_id != order.order_id or signed.pulse != order.pulse:
        issues.append('order binding')
    if signed.profile_key != order.profile_key:
        issues.append('profile version')
    if signed.receipt.order_id != order.order_id or signed.receipt.pulse != order.pulse:
        issues.append('first receipt binding')

    rows = build_provenance_view(signed)
    if (len(rows) != order.segment_count
            or any(row.position != position for position, row in enumerate(rows, 1))):
        issues.append('segment positions')
    if any(not row.origin.matches(order.pulse, row.position) for row in rows):
        issues.append('device provenance')
    if not rows or signed.measurement.peak_kpa != max(row.pressure_kpa for row in rows):
        issues.append('pressure peak')
    return Reconciliation(tuple(issues))
