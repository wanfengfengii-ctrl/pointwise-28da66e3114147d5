from __future__ import annotations

from dataclasses import dataclass

from archive.ledger import ensure_ledger_store
from archive.results import SignedResult, ensure_result_store, find_signed_result
from capture.orders import (
    CaptureOrder, ensure_order_store, get_order, is_cancelled, save_new_order,
    update_custody, validate_order_id, validate_shift,
)
from capture.release import release_after_publish
from device.retention import RetentionReplyLost, query_retention, request_retention
from spec.initialize import initialize_profiles
from spec.versions import get_version
from storage import require_idle, transaction


class OrderConflict(ValueError):
    pass


@dataclass(frozen=True)
class Registration:
    order: CaptureOrder
    status: str
    signed: SignedResult | None = None


def initialize_station(connection):
    initialize_profiles(connection)
    with transaction(connection, write=True):
        ensure_order_store(connection)
        ensure_ledger_store(connection)
        ensure_result_store(connection)


def refresh_custody(station, recorder, order, *, lose_reply=False, hide_status=False):
    if (recorder is None or order.custody in ('released', 'unavailable')
            or is_cancelled(station, order.order_id)):
        return order
    try:
        record = query_retention(recorder, order.retention_id, hide_status=hide_status)
        if record is None:
            record = request_retention(
                recorder, order.retention_id, order.pulse, lose_reply=lose_reply
            )
    except RetentionReplyLost:
        return get_order(station, order.order_id)
    if record.pulse != order.pulse:
        return get_order(station, order.order_id)
    with transaction(station, write=True):
        return update_custody(station, order.order_id, record.status.value)


def register(station, recorder, order_id, pulse, segment_count, profile_key, *,
             shift, lose_reply=False, hide_status=False):
    validate_order_id(order_id)
    validate_shift(shift)
    pulse.validate()
    if type(segment_count) is not int or segment_count <= 0:
        raise ValueError('segment count must be a positive integer')
    if (not isinstance(profile_key, tuple) or len(profile_key) != 2
            or any(not isinstance(part, str) or not part.strip() for part in profile_key)):
        raise ValueError('a line and fixed profile revision are required')
    if recorder is not None:
        require_idle(recorder)
    with transaction(station, write=True):
        order = get_order(station, order_id)
        if order is None:
            profile = get_version(station, profile_key)
            if profile is None or segment_count != profile.segment_count:
                raise ValueError('unknown profile or mismatched segment count')
            order = save_new_order(station, CaptureOrder(
                order_id, pulse, segment_count, profile_key, shift, 'capture/' + order_id
            ))
        elif order.binding != (pulse, segment_count, profile_key):
            raise OrderConflict('order number already has a different capture binding')
        cancelled = is_cancelled(station, order_id)

    if not cancelled and find_signed_result(station, order_id) is None:
        order = refresh_custody(
            station, recorder, order, lose_reply=lose_reply, hide_status=hide_status
        )
    signed = find_signed_result(station, order_id)
    if signed is not None:
        if recorder is not None:
            release_after_publish(station, recorder, order_id)
            order = get_order(station, order_id)
        return Registration(order, 'signed', signed)
    if cancelled or is_cancelled(station, order_id):
        return Registration(order, 'cancelled')
    status = 'pending_confirmation' if order.custody == 'pending' else 'pending'
    return Registration(order, status)
