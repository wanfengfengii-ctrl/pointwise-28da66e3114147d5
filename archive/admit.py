from archive.ledger import AdmittedSegment, store_admitted_segment
from device.segments import SegmentOrigin


class RejectedSegment(ValueError):
    pass


def validate_segment_origin(order, segment):
    if segment.pulse != order.pulse:
        raise RejectedSegment('segment belongs to another physical pulse')
    if (not isinstance(segment.origin, SegmentOrigin)
            or not segment.origin.matches(order.pulse, segment.position)):
        raise RejectedSegment('origin does not match the pulse and position')


def admit_segment(connection, order, profile, segment):
    if not profile.allows_position(segment.position):
        raise RejectedSegment('position outside the ordered profile')
    validate_segment_origin(order, segment)
    if segment.raw_code is None:
        return None
    if not profile.allows_raw_code(segment.raw_code):
        raise RejectedSegment('raw code outside the ordered profile')
    return store_admitted_segment(connection, AdmittedSegment(
        order.order_id, segment.pulse, segment.position, segment.raw_code, segment.origin
    ))
