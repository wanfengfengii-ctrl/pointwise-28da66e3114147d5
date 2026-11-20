from archive.results import find_signed_result
from capture.orders import get_order, update_custody
from device.retention import RetentionStatus, query_retention, release_retention
from storage import require_idle, transaction


def release_after_publish(station, recorder, order_id):
    require_idle(recorder)
    with transaction(station):
        order = get_order(station, order_id)
        signed = find_signed_result(station, order_id)
        if order is None or signed is None:
            return False
        receipt = signed.receipt
        if (signed.order_id != order.order_id or signed.pulse != order.pulse
                or signed.profile_key != order.profile_key
                or receipt.order_id != order.order_id or receipt.pulse != order.pulse):
            return False

    record = query_retention(recorder, order.retention_id)
    if record is None or record.pulse != order.pulse:
        return False
    released = release_retention(recorder, order.retention_id, order.pulse)
    if released.status is not RetentionStatus.RELEASED:
        return False
    with transaction(station, write=True):
        update_custody(station, order_id, released.status.value)
    return True
