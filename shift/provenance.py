from dataclasses import dataclass

from device.segments import SegmentOrigin


@dataclass(frozen=True)
class ProvenanceRow:
    profile_key: tuple[str, str]
    position: int
    raw_code: int
    pressure_kpa: int
    origin: SegmentOrigin


def build_provenance_view(signed):
    return tuple(
        ProvenanceRow(
            signed.profile_key, segment.position, segment.raw_code, segment.pressure_kpa, segment.origin
        )
        for segment in signed.measurement.segments
    )
