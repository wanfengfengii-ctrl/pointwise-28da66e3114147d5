from __future__ import annotations

from archive.results import find_signed_result
from capture.orders import get_order, is_cancelled, mark_cancelled, update_custody
from device.retention import RetentionReplyLost, clear_retention
from shift.lookup import lookup
from storage import require_idle, transaction


def cancel(station, order_id, recorder=None):
    """Cancel one capture order; the station database decides the winner.

    A committed signature wins and is returned unchanged.  Otherwise the
    cancellation is recorded, and both later re-registration and resumption
    report ``cancelled``.  An optional device connection lets the call also
    clear the original retention, but the returned view is the station verdict.
    """
    if recorder is not None:
        require_idle(recorder)

    with transaction(station, write=True):
        order = get_order(station, order_id)
        if order is None:
            verdict_is_cancelled = False
        elif find_signed_result(station, order_id) is not None:
            # A committed signature already won this order; cancel is a no-op replay.
            verdict_is_cancelled = False
        else:
            mark_cancelled(station, order_id)
            verdict_is_cancelled = True

    if order is None:
        return lookup(station, order_id)
    if verdict_is_cancelled and recorder is not None:
        _clear_quietly(station, recorder, order.order_id, order.retention_id, order.pulse)
    return lookup(station, order_id)


def cleanup_cancelled(station, recorder, order_id):
    """Retry clearing the original retention of a cancelled order.

    Returns ``pending`` while the device answer is unavailable and ``cleared``
    once the original request can no longer hold or regain custody.  Unknown
    orders raise ``KeyError``; orders that were not cancelled raise ``ValueError``.
    """
    order = get_order(station, order_id)
    if order is None:
        raise KeyError(order_id)
    if not is_cancelled(station, order_id):
        raise ValueError('only a cancelled order has retention to clean up')
    if recorder is None:
        return 'pending'
    require_idle(recorder)
    try:
        result = clear_retention(recorder, order.retention_id, order.pulse)
    except RetentionReplyLost:
        return 'pending'
    if result != 'cleared':
        return 'pending'
    with transaction(station, write=True):
        update_custody(station, order_id, 'released')
    return 'cleared'


def _clear_quietly(station, recorder, order_id, retention_id, pulse):
    try:
        clear_retention(recorder, retention_id, pulse)
    except (RetentionReplyLost, ValueError):
        return 'pending'
    with transaction(station, write=True):
        update_custody(station, order_id, 'released')
    return 'cleared'
