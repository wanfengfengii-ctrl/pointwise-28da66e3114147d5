from dataclasses import dataclass

from device.readout import read_retained_segments
from device.segments import RecordedSegment


@dataclass(frozen=True)
class EvidenceBatch:
    segments: tuple[RecordedSegment, ...]
    seen_positions: tuple[int, ...]
    missing_positions: tuple[int, ...]


def collect_order_evidence(recorder, order):
    readout = read_retained_segments(recorder, order.retention_id, order.pulse)
    expected = range(1, order.segment_count + 1)
    seen = {
        segment.position for segment in readout.segments
        if segment.raw_code is not None and segment.position in expected
    }
    return EvidenceBatch(
        readout.segments, tuple(sorted(seen)),
        tuple(position for position in expected if position not in seen)
    )
