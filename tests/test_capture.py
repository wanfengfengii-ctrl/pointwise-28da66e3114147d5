from dataclasses import replace

from archive.ledger import list_admitted_segments
from archive.publish import receive_and_publish
from capture.evidence import collect_order_evidence
from capture.orders import get_order
from capture.register import OrderConflict, register
from capture.resume import resume
from device.pulses import PulseIdentity, get_active_pulse
from device.readout import RetentionNotReadable, read_retained_segments
from device.retention import RetentionStatus, query_retention
from device.segments import RecordedSegment, RecordingConflict, list_recorded_segments, record_segment
from shift.lookup import lookup
from spec.versions import configure
from tests.support import StationCase


class CaptureTests(StationCase):
    def test_complete_pulse(self):
        self.seed_first_segment()
        order = self.register().order
        self.assertEqual(collect_order_evidence(self.device, order).missing_positions, (2,))
        self.assertEqual(resume(self.station, self.device, order.order_id).status, 'pending')
        self.assertEqual(get_order(self.station, order.order_id).received_count, 1)
        self.record(self.p, 2, 11)
        view = resume(self.station, self.device, order.order_id)
        self.assert_p_measurement(view)
        self.assertEqual(view.signed.receipt.receipt_id, 'receipt/P-order')
        self.assertEqual(resume(self.station, self.device, order.order_id), view)
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)

    def test_tail_survives_next_trigger(self):
        self.seed_first_segment()
        order = self.register().order
        self.assertEqual(order.custody, 'held')
        self.assertEqual(resume(self.station, self.device, order.order_id).status, 'pending')
        self.trigger(self.q)
        active = get_active_pulse(self.device)
        self.assertEqual((active.identity, active.slot), (self.q, self.slot))
        self.record(self.q, 1, 13)
        self.record(self.q, 2, 29)
        self.record(self.p, 2, 11)
        self.register('Q-order', self.q)
        original = collect_order_evidence(self.device, order)
        self.assertEqual(original.seen_positions, (1, 2))
        self.assertEqual(tuple(segment.pulse for segment in original.segments), (self.p, self.p))
        self.assert_p_measurement(resume(self.station, self.device, order.order_id))
        other = resume(self.station, self.device, 'Q-order')
        self.assertEqual(other.signed.measurement.readings_kpa, (13, 29))
        self.assertEqual(other.signed.measurement.peak_kpa, 29)
        self.assertEqual(tuple(row.origin.pulse for row in other.provenance), (self.q, self.q))

    def test_request_identity(self):
        self.seed_first_segment()
        order = self.register().order
        newer = configure(self.station, replace(self.profile, revision='v2', slope=9))
        self.assertEqual(self.register().order, order)
        for pulse, count, key in (
            (self.q, 2, self.profile.key), (self.p, 3, self.profile.key), (self.p, 2, newer.key),
        ):
            with self.subTest(binding=(pulse, count, key)), self.assertRaises(OrderConflict):
                register(self.station, self.device, order.order_id, pulse, count, key, shift='night-B')
        self.assertEqual(get_order(self.station, order.order_id), order)
        self.record(self.p, 2, 11)
        signed = resume(self.station, self.device, order.order_id).signed
        self.reopen(device=False)
        replay = self.register(shift='night-B')
        self.assertEqual(replay.status, 'signed')
        self.assertEqual(replay.order.shift, self.shift)
        self.assertEqual(replay.order.retention_id, order.retention_id)
        self.assertEqual(replay.signed, signed)
        with self.assertRaises(OrderConflict):
            self.register(profile=newer)

    def test_device_replay(self):
        first = self.seed_first_segment()
        self.assertEqual(self.record(self.p, 1, 7), first)
        with self.assertRaises(RecordingConflict):
            self.record(self.p, 1, 8)
        with self.assertRaises(RecordingConflict):
            record_segment(self.device, self.p, 1, 7, replace(first.origin, source='other'))
        self.assertEqual(list_recorded_segments(self.device, self.p), (first,))
        self.assertIsNone(self.record(self.p, 1, None))
        self.assertEqual(list_recorded_segments(self.device, self.p), (first,))

    def test_archive_replay(self):
        first = self.seed_first_segment()
        order = self.register().order
        initial = receive_and_publish(self.station, order.order_id, [first, first])
        self.assertEqual((initial.missing_positions, initial.rejected), ((2,), ()))
        original = list_admitted_segments(self.station, order.order_id)
        self.assertEqual(len(original), 1)
        conflicts = [replace(first, raw_code=8), replace(first, origin=replace(first.origin, source='other'))]
        retry = receive_and_publish(self.station, order.order_id, conflicts)
        self.assertIsNone(retry.signed)
        self.assertEqual([item.position for item in retry.rejected], [1, 1])
        self.assertEqual(list_admitted_segments(self.station, order.order_id), original)
        self.record(self.p, 2, 11)
        signed = resume(self.station, self.device, order.order_id).signed
        late = receive_and_publish(self.station, order.order_id, [first, conflicts[0]])
        self.assertEqual(late.signed, signed)
        self.assertEqual(len(late.rejected), 1)

    def test_empty_and_zero(self):
        first = self.seed_first_segment()
        self.register()
        empty = RecordedSegment(self.p, 2, None, self.origin(self.p, 2))
        self.assertIsNone(self.record(self.p, 2, None))
        missing = receive_and_publish(self.station, 'P-order', [first, empty])
        self.assertEqual(missing.missing_positions, (2,))
        self.assertEqual(missing.rejected, ())
        self.assertIsNone(missing.signed)
        self.assertEqual(lookup(self.station, 'P-order').status, 'pending')
        self.record(self.p, 2, 0)
        view = resume(self.station, self.device, 'P-order')
        self.assertEqual(view.signed.measurement.readings_kpa, (7, 0))
        self.assertEqual(view.provenance[1].raw_code, 0)
        self.assertEqual(view.signed.measurement.peak_kpa, 7)

    def test_raw_domain(self):
        self.trigger(self.p)
        self.register()
        for position, raw in ((0, 7), (3, 7), (1, 1001), (1, -1001), (2, True), (2, 1.0)):
            with self.subTest(position=position, raw=raw):
                with self.assertRaises(ValueError):
                    self.record(self.p, position, raw)
                incoming = RecordedSegment(self.p, position, raw, self.origin(self.p, position))
                publication = receive_and_publish(self.station, 'P-order', [incoming])
                self.assertEqual(len(publication.rejected), 1)
                self.assertEqual(publication.missing_positions, (1, 2))
        self.assertEqual(list_recorded_segments(self.device, self.p), ())
        self.assertEqual(list_admitted_segments(self.station, 'P-order'), ())
        with self.assertRaises(ValueError):
            register(self.station, self.device, 'bad-count', self.p, 3, self.profile.key, shift=self.shift)
        self.assertIsNone(get_order(self.station, 'bad-count'))

    def test_affine_order_peak(self):
        scenarios = (
            (self.p, 'positive', -3, (19, 11), 19),
            (self.q, 'negative', -30, (-8, -16), -8),
        )
        for pulse, revision, intercept, readings, peak in scenarios:
            with self.subTest(revision=revision):
                profile = configure(self.station, replace(
                    self.profile, revision=revision, slope=2, intercept=intercept
                ))
                self.trigger(pulse, profile=profile)
                self.register(revision, pulse, profile=profile)
                second = self.record(pulse, 2, 7)
                first = self.record(pulse, 1, 11)
                published = receive_and_publish(self.station, revision, [second, first])
                self.assertEqual(published.rejected, ())
                view = resume(self.station, self.device, revision)
                self.assertEqual(view.signed.measurement.readings_kpa, readings)
                self.assertEqual(view.signed.measurement.peak_kpa, peak)
                self.assertEqual(tuple(row.position for row in view.provenance), (1, 2))
                self.assertEqual(tuple(row.origin for row in view.provenance), (first.origin, second.origin))
                self.assertTrue(view.reconciliation.consistent)

    def test_exact_integer_survives_offline_round_trip(self):
        large = 2 ** 16000
        profile = configure(self.station, replace(
            self.profile, revision='wide', raw_max=large, slope=3, intercept=-2
        ))
        self.trigger(self.p, profile=profile)
        self.register(profile=profile)
        self.record(self.p, 1, large)
        self.record(self.p, 2, 0)
        resume(self.station, self.device, 'P-order')
        self.reopen(device=False)
        view = lookup(self.station, 'P-order')
        self.assertEqual(view.signed.measurement.readings_kpa, (3 * large - 2, -2))
        self.assertEqual(view.signed.measurement.peak_kpa, 3 * large - 2)
        self.assertEqual(view.provenance[0].raw_code, large)

    def test_unretained_pulse_is_retired_on_slot_advance(self):
        self.seed_first_segment()
        self.trigger(self.q)
        self.assertEqual(list_recorded_segments(self.device, self.p), ())
        with self.assertRaises(ValueError):
            self.record(self.p, 2, 11)
        with self.assertRaises(ValueError):
            self.trigger(self.p)
        self.assertEqual(get_active_pulse(self.device).identity, self.q)
        order = self.register().order
        self.assertEqual(query_retention(self.device, order.retention_id).status, RetentionStatus.UNAVAILABLE)

    def test_releasing_one_request_preserves_another_hold(self):
        self.seed_first_segment()
        first = self.register().order
        other = self.register('P-copy').order
        next_pulse = PulseIdentity('recorder-1', 'next')
        self.trigger(next_pulse)
        self.record(self.p, 2, 11)
        self.assert_p_measurement(resume(self.station, self.device, first.order_id))
        with self.assertRaises(RetentionNotReadable):
            read_retained_segments(self.device, first.retention_id, self.p)
        self.assertEqual(len(read_retained_segments(self.device, other.retention_id, self.p).segments), 2)
        self.assert_p_measurement(resume(self.station, self.device, other.order_id))
        self.assertEqual(list_recorded_segments(self.device, self.p), ())
