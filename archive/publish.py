from __future__ import annotations

from dataclasses import dataclass

from archive.admit import RejectedSegment, admit_segment
from archive.completeness import complete_segments, missing_positions
from archive.ledger import AdmissionConflict, list_admitted_segments
from archive.measurement import measure_pulse
from archive.results import (
    FirstReceipt, SignedResult, find_signed_result, store_first_receipt, store_result,
)
from capture.orders import get_order, save_handoff_progress
from spec.versions import get_version
from storage import transaction


class PublicationInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class RejectedEvidence:
    position: int
    reason: str


@dataclass(frozen=True)
class Publication:
    signed: SignedResult | None
    missing_positions: tuple[int, ...]
    rejected: tuple[RejectedEvidence, ...]


def receive_and_publish(connection, order_id, segments, *, interrupt_publication=False):
    with transaction(connection, write=True):
        order = get_order(connection, order_id)
        if order is None:
            raise KeyError(order_id)
        profile = get_version(connection, order.profile_key)
        if profile is None or profile.segment_count != order.segment_count:
            raise RuntimeError('order does not have its original profile')

        rejected = []
        for segment in segments:
            try:
                admit_segment(connection, order, profile, segment)
            except (RejectedSegment, AdmissionConflict) as error:
                rejected.append(RejectedEvidence(segment.position, str(error)))

        existing = find_signed_result(connection, order_id)
        if existing is not None:
            return Publication(existing, (), tuple(rejected))

        admitted = list_admitted_segments(connection, order_id)
        save_handoff_progress(connection, order_id, len(admitted))
        ordered = complete_segments(profile, admitted)
        if ordered is None or order.custody != 'held':
            return Publication(None, missing_positions(profile, admitted), tuple(rejected))

        measurement = measure_pulse(profile, ordered)
        receipt = FirstReceipt('receipt/' + order_id, order_id, order.pulse)
        signed = SignedResult(order_id, order.pulse, order.profile_key, measurement, receipt)
        store_result(connection, signed)
        if interrupt_publication:
            raise PublicationInterrupted('archive interrupted between result and first receipt')
        store_first_receipt(connection, receipt)
        return Publication(signed, (), tuple(rejected))
