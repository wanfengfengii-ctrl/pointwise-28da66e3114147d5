from dataclasses import dataclass

from archive.measurement import MeasuredSegment, PulseMeasurement
from device.pulses import PulseIdentity
from device.segments import SegmentOrigin


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: str
    order_id: str
    pulse: PulseIdentity


@dataclass(frozen=True)
class SignedResult:
    order_id: str
    pulse: PulseIdentity
    profile_key: tuple[str, str]
    measurement: PulseMeasurement
    receipt: FirstReceipt


def ensure_result_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS signed_results (
            order_id TEXT PRIMARY KEY REFERENCES capture_orders(order_id),
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            line TEXT NOT NULL,
            revision TEXT NOT NULL,
            peak_kpa TEXT NOT NULL,
            FOREIGN KEY (line, revision) REFERENCES profile_versions(line, revision)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS signed_segments (
            order_id TEXT NOT NULL REFERENCES signed_results(order_id),
            position TEXT NOT NULL,
            raw_code TEXT NOT NULL,
            pressure_kpa TEXT NOT NULL,
            origin_recorder TEXT NOT NULL,
            origin_pulse TEXT NOT NULL,
            origin_position TEXT NOT NULL,
            slot TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (order_id, position)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS first_receipts (
            order_id TEXT PRIMARY KEY REFERENCES signed_results(order_id),
            receipt_id TEXT NOT NULL UNIQUE,
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL
        )
    ''')


def store_result(connection, result):
    connection.execute('''
        INSERT INTO signed_results (order_id, recorder, pulse, line, revision, peak_kpa)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        result.order_id, *result.pulse.key, *result.profile_key, hex(result.measurement.peak_kpa)
    ))
    connection.executemany('''
        INSERT INTO signed_segments
            (order_id, position, raw_code, pressure_kpa, origin_recorder,
             origin_pulse, origin_position, slot, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        (result.order_id, hex(segment.position), hex(segment.raw_code), hex(segment.pressure_kpa),
         *segment.origin.pulse.key, hex(segment.origin.position), segment.origin.slot,
         segment.origin.source)
        for segment in result.measurement.segments
    ))


def store_first_receipt(connection, receipt):
    connection.execute('''
        INSERT INTO first_receipts (order_id, receipt_id, recorder, pulse) VALUES (?, ?, ?, ?)
    ''', (receipt.order_id, receipt.receipt_id, *receipt.pulse.key))


def _measured_from_row(row):
    origin = SegmentOrigin(
        PulseIdentity(row['origin_recorder'], row['origin_pulse']),
        int(row['origin_position'], 16), row['slot'], row['source']
    )
    return MeasuredSegment(
        int(row['position'], 16), int(row['raw_code'], 16), int(row['pressure_kpa'], 16), origin
    )


def find_signed_result(connection, order_id):
    row = connection.execute('''
        SELECT r.*, f.receipt_id, f.recorder AS receipt_recorder, f.pulse AS receipt_pulse
        FROM signed_results AS r JOIN first_receipts AS f USING (order_id)
        WHERE r.order_id = ?
    ''', (order_id,)).fetchone()
    if row is None:
        return None
    segment_rows = connection.execute('''
        SELECT * FROM signed_segments WHERE order_id = ?
    ''', (order_id,))
    segments = tuple(sorted(
        (_measured_from_row(segment) for segment in segment_rows), key=lambda segment: segment.position
    ))
    return SignedResult(
        order_id, PulseIdentity(row['recorder'], row['pulse']), (row['line'], row['revision']),
        PulseMeasurement(segments, int(row['peak_kpa'], 16)),
        FirstReceipt(row['receipt_id'], order_id, PulseIdentity(row['receipt_recorder'], row['receipt_pulse']))
    )
