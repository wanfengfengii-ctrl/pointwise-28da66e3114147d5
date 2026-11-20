from dataclasses import replace

from archive.ledger import list_admitted_segments
from archive.publish import receive_and_publish
from capture.resume import resume
from device.pulses import PulseIdentity
from shift.lookup import lookup
from tests.support import StationCase


class AdmissionTests(StationCase):
    def test_foreign_tail(self):
        first = self.seed_first_segment()
        self.register()
        self.assertEqual(resume(self.station, self.device, 'P-order').status, 'pending')
        baseline = list_admitted_segments(self.station, 'P-order')
        self.assertEqual(tuple(item.origin for item in baseline), (first.origin,))

        for pulse, raw_code in ((self.q, 29), (PulseIdentity('recorder-1', 'R'), 11)):
            with self.subTest(pulse=pulse):
                self.trigger(pulse)
                foreign = self.record(pulse, 2, raw_code)
                publication = receive_and_publish(self.station, 'P-order', [foreign])
                self.assertEqual(publication.missing_positions, (2,))
                self.assertEqual(len(publication.rejected), 1)
                self.assertIsNone(publication.signed)
                view = lookup(self.station, 'P-order')
                self.assertEqual(view.status, 'pending')
                self.assertIsNone(view.signed)
                self.assertEqual(list_admitted_segments(self.station, 'P-order'), baseline)

        tail = self.record(self.p, 2, 11)
        publication = receive_and_publish(self.station, 'P-order', [tail])
        self.assertEqual((publication.missing_positions, publication.rejected), ((), ()))
        self.assert_p_measurement(resume(self.station, self.device, 'P-order'))

    def test_origin_must_match_segment(self):
        self.seed_first_segment()
        self.register()
        self.assertEqual(resume(self.station, self.device, 'P-order').status, 'pending')
        tail = self.record(self.p, 2, 11)
        for origin in (
            replace(tail.origin, pulse=self.q), replace(tail.origin, position=1),
            replace(tail.origin, source=' '), replace(tail.origin, slot=''),
        ):
            with self.subTest(origin=origin):
                publication = receive_and_publish(self.station, 'P-order', [replace(tail, origin=origin)])
                self.assertEqual(len(publication.rejected), 1)
                self.assertEqual(publication.missing_positions, (2,))
                self.assertIsNone(publication.signed)
        self.assert_p_measurement(resume(self.station, self.device, 'P-order'))
