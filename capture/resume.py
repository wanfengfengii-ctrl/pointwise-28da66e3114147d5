from archive.publish import receive_and_publish
from capture.evidence import collect_order_evidence
from capture.orders import get_order
from capture.register import refresh_custody
from capture.release import release_after_publish
from device.readout import RetentionNotReadable
from shift.lookup import lookup
from storage import require_idle


def resume(station, recorder, order_id, *, hide_status=False, interrupt_publication=False):
    view = lookup(station, order_id)
    if recorder is None or view.status in ('unknown', 'cancelled'):
        return view
    require_idle(recorder)
    if view.status == 'signed':
        release_after_publish(station, recorder, order_id)
        return view

    order = refresh_custody(station, recorder, get_order(station, order_id), hide_status=hide_status)
    if order.custody != 'held':
        return lookup(station, order_id)
    try:
        evidence = collect_order_evidence(recorder, order)
    except RetentionNotReadable:
        # Another session may have signed and released these originals.
        return lookup(station, order_id)
    publication = receive_and_publish(
        station, order_id, evidence.segments, interrupt_publication=interrupt_publication
    )
    if publication.signed is not None:
        release_after_publish(station, recorder, order_id)
    return lookup(station, order_id)
