from dataclasses import dataclass
from enum import Enum

from device.pulses import PulseIdentity, get_pulse, retire_pulse
from storage import transaction


class RetentionStatus(str, Enum):
    HELD = 'held'
    UNAVAILABLE = 'unavailable'
    RELEASED = 'released'


class RetentionReplyLost(TimeoutError):
    pass


@dataclass(frozen=True)
class RetentionRecord:
    request_id: str
    pulse: PulseIdentity
    status: RetentionStatus


def ensure_retention_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS retention_requests (
            request_id TEXT PRIMARY KEY,
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('held', 'unavailable', 'released'))
        )
    ''')
    connection.execute('''
        CREATE INDEX IF NOT EXISTS retention_by_pulse
        ON retention_requests (recorder, pulse, status)
    ''')


def query_retention(connection, request_id, *, hide_status=False):
    if hide_status:
        raise RetentionReplyLost('retention status is temporarily unavailable')
    row = connection.execute('''
        SELECT recorder, pulse, status FROM retention_requests WHERE request_id = ?
    ''', (request_id,)).fetchone()
    if row is None:
        return None
    return RetentionRecord(
        request_id, PulseIdentity(row['recorder'], row['pulse']), RetentionStatus(row['status'])
    )


def is_pulse_retained(connection, pulse):
    return connection.execute('''
        SELECT 1 FROM retention_requests
        WHERE recorder = ? AND pulse = ? AND status = 'held' LIMIT 1
    ''', pulse.key).fetchone() is not None


def request_retention(connection, request_id, pulse, *, lose_reply=False):
    pulse.validate()
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('a retention request identifier is required')
    with transaction(connection, write=True):
        record = query_retention(connection, request_id)
        if record is not None and record.pulse != pulse:
            raise ValueError('retention request is bound to another pulse')
        if record is None:
            physical = get_pulse(connection, pulse)
            status = (
                RetentionStatus.HELD if physical is not None and physical.recordable
                else RetentionStatus.UNAVAILABLE
            )
            connection.execute('''
                INSERT INTO retention_requests (request_id, recorder, pulse, status)
                VALUES (?, ?, ?, ?)
            ''', (request_id, *pulse.key, status.value))
            record = RetentionRecord(request_id, pulse, status)
    if lose_reply:
        raise RetentionReplyLost('retention reply lost after device commit')
    return record


def release_retention(connection, request_id, pulse):
    with transaction(connection, write=True):
        record = query_retention(connection, request_id)
        if record is None:
            return None
        if record.pulse != pulse:
            raise ValueError('retention request is bound to another pulse')
        if record.status is not RetentionStatus.HELD:
            return record
        connection.execute('''
            UPDATE retention_requests SET status = 'released' WHERE request_id = ?
        ''', (request_id,))
        if not is_pulse_retained(connection, pulse):
            retire_pulse(connection, pulse)
        return RetentionRecord(request_id, pulse, RetentionStatus.RELEASED)


def clear_retention(connection, request_id, pulse):
    """Make one request permanently hold nothing, even if a late call arrives.

    A missing request gets a released tombstone so a delayed request for the
    same number can never create a fresh hold.  An existing hold is released.
    """
    pulse.validate()
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('a retention request identifier is required')
    with transaction(connection, write=True):
        record = query_retention(connection, request_id)
        if record is not None and record.pulse != pulse:
            raise ValueError('retention request is bound to another pulse')
        if record is None:
            connection.execute('''
                INSERT INTO retention_requests (request_id, recorder, pulse, status)
                VALUES (?, ?, ?, 'released')
            ''', (request_id, *pulse.key))
        elif record.status is RetentionStatus.HELD:
            connection.execute('''
                UPDATE retention_requests SET status = 'released' WHERE request_id = ?
            ''', (request_id,))
            if not is_pulse_retained(connection, pulse):
                retire_pulse(connection, pulse)
    return 'cleared'
