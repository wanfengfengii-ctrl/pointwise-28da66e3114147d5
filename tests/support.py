from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from capture.register import initialize_station, register
from capture.resume import resume
from device.pulses import PulseIdentity
from device.segments import SegmentOrigin, record_segment
from device.triggers import initialize_device, trigger_pulse
from spec.initialize import DEFAULT_PROFILES
from spec.versions import get_version
from storage import open_database


class StationCase(unittest.TestCase):
    shift = 'night-A'
    slot = 'slot-1'

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.station = None
        self.device = None
        self.addCleanup(self.close_stores)
        self.reopen()
        self.profile = get_version(self.station, DEFAULT_PROFILES[0].key)
        self.p = PulseIdentity('recorder-1', 'P')
        self.q = PulseIdentity('recorder-1', 'Q')

    def close_stores(self):
        for name in ('device', 'station'):
            connection = getattr(self, name)
            if connection is not None:
                connection.close()
                setattr(self, name, None)

    def reopen(self, *, device=True):
        self.close_stores()
        self.station = open_database(self.root / 'station.sqlite3')
        initialize_station(self.station)
        if device:
            self.device = open_database(self.root / 'device.sqlite3')
            initialize_device(self.device)

    def trigger(self, pulse, *, profile=None):
        return trigger_pulse(self.device, pulse, self.slot, profile or self.profile)

    def origin(self, pulse, position):
        return SegmentOrigin(pulse, position, self.slot, f'{pulse.pulse}/segment-{position}')

    def record(self, pulse, position, raw_code):
        return record_segment(self.device, pulse, position, raw_code, self.origin(pulse, position))

    def register(self, order_id='P-order', pulse=None, *, profile=None, shift=None, **options):
        profile = profile or self.profile
        return register(
            self.station, self.device, order_id, pulse or self.p,
            profile.segment_count, profile.key, shift=shift or self.shift, **options
        )

    def seed_first_segment(self):
        self.trigger(self.p)
        return self.record(self.p, 1, 7)

    def sign_pulse(self, order_id='P-order', pulse=None, *, shift=None):
        pulse = pulse or self.p
        self.trigger(pulse)
        self.record(pulse, 1, 7)
        self.register(order_id, pulse, shift=shift)
        self.record(pulse, 2, 11)
        return resume(self.station, self.device, order_id)

    def assert_p_measurement(self, view):
        self.assertEqual(view.status, 'signed')
        self.assertEqual(view.signed.measurement.readings_kpa, (7, 11))
        self.assertEqual(view.signed.measurement.peak_kpa, 11)
        self.assertEqual(view.signed.receipt.pulse, self.p)
        self.assertEqual(tuple(row.origin for row in view.provenance), (
            self.origin(self.p, 1), self.origin(self.p, 2)
        ))
        self.assertTrue(view.reconciliation.consistent)
