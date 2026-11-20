from dataclasses import replace

from capture.resume import resume
from device.pulses import PulseIdentity
from shift.lookup import lookup
from shift.manifest import manifest
from spec.versions import configure
from tests.support import StationCase


class ShiftTests(StationCase):
    def test_offline_lookup(self):
        live = self.sign_pulse()
        self.assert_p_measurement(live)
        configure(self.station, replace(self.profile, revision='v2', slope=4, intercept=-2))
        self.reopen(device=False)
        offline = lookup(self.station, 'P-order')
        self.assertEqual(offline, live)
        self.assertEqual(offline.signed.profile_key, ('fan-line', 'v1'))
        self.assertEqual(offline.signed.receipt.receipt_id, 'receipt/P-order')
        self.assertEqual(tuple(row.profile_key for row in offline.provenance), (self.profile.key,) * 2)
        self.assertEqual(tuple(row.position for row in offline.provenance), (1, 2))
        self.assertEqual(tuple(row.pressure_kpa for row in offline.provenance), (7, 11))
        self.assertEqual(tuple(row.origin for row in offline.provenance), (
            self.origin(self.p, 1), self.origin(self.p, 2)
        ))
        self.assertEqual(manifest(self.station, self.shift)[0].receipt_id, offline.signed.receipt.receipt_id)

    def test_pending_and_unknown_and_manifest_order(self):
        signed = self.sign_pulse('m-signed')
        self.trigger(self.q)
        self.record(self.q, 1, 13)
        self.register('z-incomplete', self.q)
        self.assertEqual(resume(self.station, self.device, 'z-incomplete').status, 'pending')
        late = PulseIdentity('recorder-1', 'late')
        self.trigger(late)
        self.record(late, 1, 7)
        self.assertEqual(self.register('a-unconfirmed', late, lose_reply=True).status, 'pending_confirmation')
        other = PulseIdentity('recorder-1', 'other-shift')
        self.sign_pulse('other', other, shift='night-B')
        self.reopen(device=False)

        for order_id in ('a-unconfirmed', 'z-incomplete'):
            with self.subTest(order_id=order_id):
                pending = lookup(self.station, order_id)
                self.assertEqual(pending.status, 'pending')
                self.assertIsNone(pending.signed)
                self.assertEqual(pending.provenance, ())
        unknown = lookup(self.station, 'absent')
        self.assertEqual(unknown.status, 'unknown')
        self.assertIsNone(unknown.signed)

        entries = manifest(self.station, self.shift)
        self.assertEqual(entries, manifest(self.station, self.shift))
        self.assertEqual([(entry.order_id, entry.status, entry.receipt_id, entry.peak_kpa) for entry in entries], [
            ('a-unconfirmed', 'pending', None, None),
            ('m-signed', 'signed', signed.signed.receipt.receipt_id, 11),
            ('z-incomplete', 'pending', None, None),
        ])
        self.assertEqual(tuple(entry.pulse for entry in entries), (late, self.p, self.q))
        self.assertEqual(tuple(entry.order_id for entry in manifest(self.station, 'night-B')), ('other',))
        self.assertEqual(manifest(self.station, 'unused'), ())
        for invalid in ('', ' ', ' night-A', None):
            with self.subTest(shift=invalid), self.assertRaises(ValueError):
                manifest(self.station, invalid)
