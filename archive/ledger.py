from dataclasses import dataclass

from device.pulses import PulseIdentity
from device.segments import SegmentOrigin


@dataclass(frozen=True)
class AdmittedSegment:
    order_id: str
    pulse: PulseIdentity
    position: int
    raw_code: int
    origin: SegmentOrigin


class AdmissionConflict(ValueError):
    pass


def ensure_ledger_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS admitted_segments (
            order_id TEXT NOT NULL REFERENCES capture_orders(order_id),
            position TEXT NOT NULL,
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            raw_code TEXT NOT NULL,
            origin_recorder TEXT NOT NULL,
            origin_pulse TEXT NOT NULL,
            origin_position TEXT NOT NULL,
            slot TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (order_id, position)
        )
    ''')


def _admitted_from_row(row):
    origin = SegmentOrigin(
        PulseIdentity(row['origin_recorder'], row['origin_pulse']),
        int(row['origin_position'], 16), row['slot'], row['source']
    )
    return AdmittedSegment(
        row['order_id'], PulseIdentity(row['recorder'], row['pulse']),
        int(row['position'], 16), int(row['raw_code'], 16), origin
    )


def store_admitted_segment(connection, segment):
    position = hex(segment.position)
    row = connection.execute('''
        SELECT * FROM admitted_segments WHERE order_id = ? AND position = ?
    ''', (segment.order_id, position)).fetchone()
    if row is not None:
        original = _admitted_from_row(row)
        if original != segment:
            raise AdmissionConflict('different evidence already admitted at this position')
        return original
    origin = segment.origin
    connection.execute('''
        INSERT INTO admitted_segments
            (order_id, position, recorder, pulse, raw_code, origin_recorder,
             origin_pulse, origin_position, slot, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        segment.order_id, position, *segment.pulse.key, hex(segment.raw_code),
        *origin.pulse.key, hex(origin.position), origin.slot, origin.source
    ))
    return segment


def list_admitted_segments(connection, order_id):
    rows = connection.execute('''
        SELECT * FROM admitted_segments WHERE order_id = ?
    ''', (order_id,))
    return tuple(sorted((_admitted_from_row(row) for row in rows), key=lambda row: row.position))
