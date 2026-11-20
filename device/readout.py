from dataclasses import dataclass

from device.pulses import get_pulse
from device.retention import RetentionStatus, query_retention
from device.segments import RecordedSegment, list_recorded_segments
from storage import transaction


class RetentionNotReadable(LookupError):
    pass


@dataclass(frozen=True)
class RetainedReadout:
    segments: tuple[RecordedSegment, ...]
    missing_positions: tuple[int, ...]


def read_retained_segments(connection, request_id, pulse):
    with transaction(connection):
        record = query_retention(connection, request_id)
        if record is None or record.status is not RetentionStatus.HELD or record.pulse != pulse:
            raise RetentionNotReadable('no readable retention for the specified physical pulse')
        physical = get_pulse(connection, pulse)
        if physical is None or not physical.recordable:
            raise RetentionNotReadable('retained pulse is unavailable')
        segments = list_recorded_segments(connection, pulse)
        present = {segment.position for segment in segments}
        missing = tuple(position for position in physical.profile.positions if position not in present)
        return RetainedReadout(segments, missing)
