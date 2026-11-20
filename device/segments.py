from __future__ import annotations

from dataclasses import dataclass

from device.pulses import PulseIdentity, get_active_pulse, get_pulse
from device.retention import is_pulse_retained
from storage import transaction


@dataclass(frozen=True)
class SegmentOrigin:
    pulse: PulseIdentity
    position: int
    slot: str
    source: str

    def matches(self, pulse, position):
        return (
            self.pulse == pulse and type(self.position) is int and self.position == position
            and isinstance(self.slot, str) and bool(self.slot.strip())
            and isinstance(self.source, str) and bool(self.source.strip())
        )


@dataclass(frozen=True)
class RecordedSegment:
    pulse: PulseIdentity
    position: int
    raw_code: int | None
    origin: SegmentOrigin


class RecordingConflict(ValueError):
    pass


def ensure_segment_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS device_segments (
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            position TEXT NOT NULL,
            raw_code TEXT NOT NULL,
            slot TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (recorder, pulse, position),
            FOREIGN KEY (recorder, pulse)
                REFERENCES recordable_pulses(recorder, pulse) ON DELETE CASCADE
        )
    ''')


def _segment_from_row(pulse, row):
    position = int(row['position'], 16)
    origin = SegmentOrigin(pulse, position, row['slot'], row['source'])
    return RecordedSegment(pulse, position, int(row['raw_code'], 16), origin)


def list_recorded_segments(connection, pulse):
    rows = connection.execute('''
        SELECT position, raw_code, slot, source FROM device_segments
        WHERE recorder = ? AND pulse = ?
    ''', pulse.key)
    return tuple(sorted(
        (_segment_from_row(pulse, row) for row in rows), key=lambda segment: segment.position
    ))


def record_segment(connection, pulse, position, raw_code, origin):
    pulse.validate()
    with transaction(connection, write=True):
        physical = get_pulse(connection, pulse)
        if physical is None or not physical.recordable:
            raise ValueError('the physical pulse is no longer recordable')
        active = get_active_pulse(connection)
        if (active is None or active.identity != pulse) and not is_pulse_retained(connection, pulse):
            raise ValueError('the pulse owns neither the active slot nor a retention')
        if not physical.profile.allows_position(position):
            raise ValueError('position outside the recording profile')
        if not isinstance(origin, SegmentOrigin) or not origin.matches(pulse, position):
            raise ValueError('origin does not match the recorded pulse and position')
        if origin.slot != physical.slot:
            raise ValueError('origin slot does not match the physical recording')
        if raw_code is None:
            return None
        if not physical.profile.allows_raw_code(raw_code):
            raise ValueError('raw code outside the recording interval')
        incoming = RecordedSegment(pulse, position, raw_code, origin)
        row = connection.execute('''
            SELECT position, raw_code, slot, source FROM device_segments
            WHERE recorder = ? AND pulse = ? AND position = ?
        ''', (*pulse.key, hex(position))).fetchone()
        if row is not None:
            original = _segment_from_row(pulse, row)
            if original != incoming:
                raise RecordingConflict('recording already has different evidence at this position')
            return original
        connection.execute('''
            INSERT INTO device_segments (recorder, pulse, position, raw_code, slot, source)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (*pulse.key, hex(position), hex(raw_code), origin.slot, origin.source))
        return incoming
