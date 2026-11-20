from archive.ledger import list_admitted_segments
from archive.publish import PublicationInterrupted, receive_and_publish
from capture.orders import get_order
from capture.release import release_after_publish
from capture.resume import resume
from device.readout import RetentionNotReadable, read_retained_segments
from device.retention import RetentionStatus, query_retention
from shift.lookup import lookup
from tests.support import StationCase


class RecoveryTests(StationCase):
    def test_recover_retention(self):
        self.seed_first_segment()
        pending = self.register(lose_reply=True)
        self.assertEqual(pending.status, 'pending_confirmation')
        self.assertEqual(pending.order.custody, 'pending')
        self.assertIsNone(pending.signed)
        request = query_retention(self.device, pending.order.retention_id)
        self.assertEqual((request.pulse, request.status), (self.p, RetentionStatus.HELD))

        self.reopen()
        self.trigger(self.q)
        self.record(self.q, 1, 13)
        self.record(self.q, 2, 29)
        self.record(self.p, 2, 11)
        self.assertEqual(self.register(hide_status=True).status, 'pending_confirmation')
        unknown_reply = resume(self.station, self.device, 'P-order', hide_status=True)
        self.assertEqual(unknown_reply.status, 'pending')
        self.assertIsNone(unknown_reply.signed)
        self.assertEqual(list_admitted_segments(self.station, 'P-order'), ())
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)

        self.reopen()
        replay = self.register()
        self.assertEqual(replay.order.retention_id, request.request_id)
        self.assertEqual(replay.order.custody, 'held')
        view = resume(self.station, self.device, 'P-order')
        self.assert_p_measurement(view)
        self.assertEqual(resume(self.station, self.device, 'P-order'), view)
        requests = self.device.execute('SELECT request_id, status FROM retention_requests').fetchall()
        self.assertEqual([tuple(row) for row in requests], [(request.request_id, 'released')])
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)
        self.register('Q-order', self.q)
        self.assertEqual(resume(self.station, self.device, 'Q-order').signed.measurement.readings_kpa, (13, 29))

    def test_archive_retry(self):
        first = self.seed_first_segment()
        order = self.register().order
        self.assertEqual(resume(self.station, self.device, order.order_id).status, 'pending')
        prior = list_admitted_segments(self.station, order.order_id)
        self.assertEqual(tuple(item.origin for item in prior), (first.origin,))
        tail = self.record(self.p, 2, 11)
        with self.assertRaises(PublicationInterrupted):
            resume(self.station, self.device, order.order_id, interrupt_publication=True)

        self.reopen()
        self.assertEqual(lookup(self.station, order.order_id).status, 'pending')
        for table in ('signed_results', 'signed_segments', 'first_receipts'):
            with self.subTest(table=table):
                self.assertEqual(self.station.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(list_admitted_segments(self.station, order.order_id), prior)
        self.assertEqual(get_order(self.station, order.order_id).received_count, 1)
        self.assertFalse(release_after_publish(self.station, self.device, order.order_id))
        retained = read_retained_segments(self.device, order.retention_id, self.p)
        self.assertEqual((retained.segments, retained.missing_positions), ((first, tail), ()))

        signed = resume(self.station, self.device, order.order_id)
        self.assert_p_measurement(signed)
        self.assertEqual(query_retention(self.device, order.retention_id).status, RetentionStatus.RELEASED)
        with self.assertRaises(RetentionNotReadable):
            read_retained_segments(self.device, order.retention_id, self.p)
        for table in ('signed_results', 'first_receipts'):
            self.assertEqual(self.station.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1)
        self.reopen(device=False)
        self.assertEqual(lookup(self.station, order.order_id), signed)

    def test_resume_can_release_after_archive_already_committed(self):
        first = self.seed_first_segment()
        order = self.register().order
        tail = self.record(self.p, 2, 11)
        signed = receive_and_publish(self.station, order.order_id, [first, tail]).signed
        self.assertEqual(query_retention(self.device, order.retention_id).status, RetentionStatus.HELD)
        self.reopen()
        self.assertEqual(resume(self.station, self.device, order.order_id).signed, signed)
        self.assertEqual(query_retention(self.device, order.retention_id).status, RetentionStatus.RELEASED)

    def test_registration_can_wait_before_any_device_request(self):
        self.seed_first_segment()
        pending = self.register(hide_status=True)
        self.assertEqual(pending.status, 'pending_confirmation')
        self.assertIsNone(query_retention(self.device, pending.order.retention_id))
        self.record(self.p, 2, 11)
        self.reopen()
        self.assert_p_measurement(resume(self.station, self.device, 'P-order'))
