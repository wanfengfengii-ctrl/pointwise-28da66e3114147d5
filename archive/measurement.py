from dataclasses import dataclass

from device.segments import SegmentOrigin
from spec.scale import to_pressure


@dataclass(frozen=True)
class MeasuredSegment:
    position: int
    raw_code: int
    pressure_kpa: int
    origin: SegmentOrigin


@dataclass(frozen=True)
class PulseMeasurement:
    segments: tuple[MeasuredSegment, ...]
    peak_kpa: int

    @property
    def readings_kpa(self):
        return tuple(segment.pressure_kpa for segment in self.segments)


def measure_pulse(profile, ordered_segments):
    measured = tuple(
        MeasuredSegment(
            segment.position, segment.raw_code, to_pressure(profile, segment.raw_code), segment.origin
        )
        for segment in ordered_segments
    )
    return PulseMeasurement(measured, max(segment.pressure_kpa for segment in measured))
