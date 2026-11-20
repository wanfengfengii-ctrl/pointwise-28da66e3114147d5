from dataclasses import dataclass

from spec.profile import ProfileVersion
from spec.versions import profile_columns, profile_from_row


@dataclass(frozen=True)
class PulseIdentity:
    recorder: str
    pulse: str

    @property
    def key(self):
        return self.recorder, self.pulse

    def validate(self):
        if any(not isinstance(part, str) or not part.strip() for part in self.key):
            raise ValueError('recorder and physical pulse names are required')
        return self


@dataclass(frozen=True)
class PhysicalPulse:
    identity: PulseIdentity
    slot: str
    profile: ProfileVersion
    recordable: bool


def ensure_pulse_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS physical_pulses (
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            slot TEXT NOT NULL,
            line TEXT NOT NULL,
            revision TEXT NOT NULL,
            segment_count TEXT NOT NULL,
            raw_min TEXT NOT NULL,
            raw_max TEXT NOT NULL,
            slope TEXT NOT NULL,
            intercept TEXT NOT NULL,
            PRIMARY KEY (recorder, pulse)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS recordable_pulses (
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            PRIMARY KEY (recorder, pulse),
            FOREIGN KEY (recorder, pulse) REFERENCES physical_pulses(recorder, pulse)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS active_pulse (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            FOREIGN KEY (recorder, pulse) REFERENCES physical_pulses(recorder, pulse)
        )
    ''')


def remember_pulse(connection, identity, slot, profile):
    connection.execute('''
        INSERT INTO physical_pulses
            (recorder, pulse, slot, line, revision, segment_count,
             raw_min, raw_max, slope, intercept)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (*identity.key, slot, *profile_columns(profile)))
    connection.execute('''
        INSERT INTO recordable_pulses (recorder, pulse) VALUES (?, ?)
    ''', identity.key)
    return PhysicalPulse(identity, slot, profile, True)


def get_pulse(connection, identity):
    row = connection.execute('''
        SELECT p.*, r.pulse IS NOT NULL AS recordable
        FROM physical_pulses AS p
        LEFT JOIN recordable_pulses AS r USING (recorder, pulse)
        WHERE p.recorder = ? AND p.pulse = ?
    ''', identity.key).fetchone()
    if row is None:
        return None
    return PhysicalPulse(identity, row['slot'], profile_from_row(row), bool(row['recordable']))


def get_active_pulse(connection):
    row = connection.execute('''
        SELECT recorder, pulse FROM active_pulse WHERE singleton = 1
    ''').fetchone()
    if row is None:
        return None
    return get_pulse(connection, PulseIdentity(row['recorder'], row['pulse']))


def set_active_pulse(connection, identity):
    connection.execute('''
        INSERT INTO active_pulse (singleton, recorder, pulse) VALUES (1, ?, ?)
        ON CONFLICT (singleton) DO UPDATE
        SET recorder = excluded.recorder, pulse = excluded.pulse
    ''', identity.key)


def retire_pulse(connection, identity):
    connection.execute('''
        DELETE FROM recordable_pulses WHERE recorder = ? AND pulse = ?
    ''', identity.key)
