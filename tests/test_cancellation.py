from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event
from unittest.mock import patch

from archive.publish import PublicationInterrupted, receive_and_publish
from capture.cancel import cancel, cleanup_cancelled
from capture.orders import get_order, is_cancelled
from capture.register import OrderConflict, register
from capture.resume import resume
from device.readout import RetentionNotReadable, read_retained_segments
from device.retention import (
    RetentionReplyLost, RetentionStatus, is_pulse_retained, query_retention, release_retention,
    request_retention,
)
from shift.lookup import lookup
from shift.manifest import manifest
from storage import open_database
from tests.support import StationCase


class CancellationTests(StationCase):
    def seed_hold(self, order_id='P-order', *, lose_reply=False):
        first = self.seed_first_segment()
        registration = self.register(order_id, lose_reply=lose_reply)
        return first, registration

    def test_cancel_offline_before_any_device_request(self):
        self.seed_first_segment()
        pending = self.register(hide_status=True)
        self.assertEqual(pending.order.custody, 'pending')
        view = cancel(self.station, 'P-order')
        self.assertEqual((view.status, view.signed), ('cancelled', None))
        self.assertEqual(cancel(self.station, 'P-order'), view)
        self.assertIsNone(query_retention(self.device, pending.order.retention_id))
        self.assertEqual(lookup(self.station, 'absent').status, 'unknown')
        self.assertEqual(cancel(self.station, 'absent').status, 'unknown')

    def test_cancel_wins_over_late_tail_and_blocks_success(self):
        first, registration = self.seed_hold()
        self.assertEqual(registration.order.custody, 'held')
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        tail = self.record(self.p, 2, 11)

        publication = receive_and_publish(self.station, 'P-order', [first, tail])
        self.assertIsNone(publication.signed)
        self.assertEqual(publication.rejected, ())
        self.assertEqual(lookup(self.station, 'P-order').signed, None)
        self.assertEqual(resume(self.station, self.device, 'P-order').status, 'cancelled')
        self.assertEqual(self.register().status, 'cancelled')
        self.assertEqual(self.register(hide_status=True).status, 'cancelled')
        with self.assertRaises(OrderConflict):
            self.register(pulse=self.q)
        with self.assertRaises(OrderConflict):
            self.register('P-order', self.q, profile=replace(self.profile, revision='v9'))
        self.assertEqual(self.station.execute('SELECT count(*) FROM signed_results').fetchone()[0], 0)
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)
        self.assertEqual(get_order(self.station, 'P-order').custody, 'held')
        self.assertEqual(query_retention(self.device, registration.order.retention_id).status,
                         RetentionStatus.HELD)

    def test_sign_first_then_cancel_returns_same_signed_result(self):
        first, registration = self.seed_hold()
        tail = self.record(self.p, 2, 11)
        signed = resume(self.station, self.device, 'P-order')
        self.assert_p_measurement(signed)
        view = cancel(self.station, 'P-order', self.device)
        self.assertEqual(view.status, 'signed')
        self.assertEqual(view.signed, signed.signed)
        self.assertEqual(view.signed.receipt.receipt_id, 'receipt/P-order')
        self.assertFalse(is_cancelled(self.station, 'P-order'))
        self.assertEqual(lookup(self.station, 'P-order'), signed)
        self.assertEqual(self.register().status, 'signed')
        self.assertEqual(query_retention(self.device, registration.order.retention_id).status,
                         RetentionStatus.RELEASED)

    def test_cancel_after_interrupted_publication_wins(self):
        first, registration = self.seed_hold()
        tail = self.record(self.p, 2, 11)
        with self.assertRaises(PublicationInterrupted):
            resume(self.station, self.device, 'P-order', interrupt_publication=True)
        self.assertEqual(lookup(self.station, 'P-order').status, 'pending')
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        self.reopen()
        retry = receive_and_publish(self.station, 'P-order', [first, tail])
        self.assertIsNone(retry.signed)
        self.assertEqual(resume(self.station, self.device, 'P-order').status, 'cancelled')
        for table in ('signed_results', 'signed_segments', 'first_receipts'):
            with self.subTest(table=table):
                self.assertEqual(self.station.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_station_commit_order_decides(self):
        first, registration = self.seed_hold()
        tail = self.record(self.p, 2, 11)
        reached = Event()
        proceed = Event()
        import archive.publish as publish_module
        original = publish_module.store_result

        def paused_store_result(connection, result):
            reached.set()
            if not proceed.wait(10):
                raise TimeoutError('publisher was not resumed')
            return original(connection, result)

        def publish_from_peer():
            with closing(open_database(self.root / 'station.sqlite3')) as peer_station, \
                    closing(open_database(self.root / 'device.sqlite3')) as peer_device:
                return resume(peer_station, peer_device, 'P-order')

        with patch.object(publish_module, 'store_result', paused_store_result):
            with ThreadPoolExecutor(1) as pool:
                authored = pool.submit(publish_from_peer)
                self.assertTrue(reached.wait(10))
                # The publisher already holds the station write transaction, so
                # this cancellation can only decide after the signature commits.
                proceed.set()
                view = cancel(self.station, 'P-order')
                self.assertEqual(view.status, 'signed')
                self.assertEqual(view.signed, authored.result(timeout=10).signed)
        self.assert_p_measurement(lookup(self.station, 'P-order'))
        self.assertEqual(self.register().status, 'signed')
        self.assertFalse(is_cancelled(self.station, 'P-order'))

    def test_cancel_before_a_paused_device_request_still_clears(self):
        first = self.seed_first_segment()
        pending = self.register(hide_status=True)
        request_id = pending.order.retention_id
        self.assertIsNone(query_retention(self.device, request_id))

        verdict = []

        def paused_request(connection, request, pulse, **options):
            self.assertEqual(connection, self.device)
            verdict.append(cancel(self.station, 'P-order', self.device))
            return request_retention(connection, request, pulse, **options)

        import capture.register as register_module
        with patch.object(register_module, 'request_retention', paused_request):
            replay = self.register()
        self.assertEqual([view.status for view in verdict], ['cancelled'])
        self.assertEqual(replay.status, 'cancelled')
        record = query_retention(self.device, request_id)
        self.assertEqual(record.status, RetentionStatus.RELEASED)
        self.assertFalse(is_pulse_retained(self.device, self.p))
        tail = self.record(self.p, 2, 11)
        self.assertIsNone(receive_and_publish(self.station, 'P-order', [first, tail]).signed)
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        late = request_retention(self.device, request_id, self.p)
        self.assertEqual(late.status, RetentionStatus.RELEASED)
        self.assertFalse(is_pulse_retained(self.device, self.p))

    def test_cleanup_clears_and_tombstones(self):
        first, registration = self.seed_hold()
        tail = self.record(self.p, 2, 11)
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        request = query_retention(self.device, registration.order.retention_id)
        self.assertEqual(request.status, RetentionStatus.RELEASED)
        self.assertFalse(is_pulse_retained(self.device, self.p))
        late = request_retention(self.device, registration.order.retention_id, self.p)
        self.assertEqual(late.status, RetentionStatus.RELEASED)
        self.assertFalse(is_pulse_retained(self.device, self.p))
        self.reopen()
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        self.assertIsNone(receive_and_publish(self.station, 'P-order', [first, tail]).signed)
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)

    def test_cleanup_after_lost_answer_is_retryable(self):
        self.seed_hold()
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        import capture.cancel as cancel_module
        attempts = []

        def lost_answer(connection, request_id, pulse):
            attempts.append(request_id)
            release_retention(connection, request_id, pulse)
            raise RetentionReplyLost('test dropped response after device operation')

        with patch.object(cancel_module, 'clear_retention', lost_answer):
            self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'pending')
        self.assertEqual(attempts, ['capture/P-order'])
        self.reopen()
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        self.reopen()
        self.assertEqual(get_order(self.station, 'P-order').custody, 'released')

    def test_cancelled_order_never_reacquires_custody(self):
        self.seed_first_segment()
        pending = self.register(hide_status=True)
        self.assertIsNone(query_retention(self.device, pending.order.retention_id))
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        # An online re-registration or resume must not issue a device request.
        self.assertEqual(self.register().status, 'cancelled')
        self.assertEqual(resume(self.station, self.device, 'P-order').status, 'cancelled')
        self.assertEqual(self.register(lose_reply=True).status, 'cancelled')
        self.assertIsNone(query_retention(self.device, pending.order.retention_id))
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        self.assertEqual(query_retention(self.device, pending.order.retention_id).status,
                         RetentionStatus.RELEASED)

    def test_cleanup_retries_across_a_station_reopen(self):
        self.seed_hold()
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        import capture.cancel as cancel_module

        def lost_answer(connection, request_id, pulse):
            release_retention(connection, request_id, pulse)
            raise RetentionReplyLost('device committed but the answer was lost')

        with patch.object(cancel_module, 'clear_retention', lost_answer):
            self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'pending')
        # Reopening the station alone keeps the retry possible under the same number.
        self.station.close()
        self.station = open_database(self.root / 'station.sqlite3')
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        # Reopening the device alone also leaves the persistent verdict readable.
        self.device.close()
        self.device = open_database(self.root / 'device.sqlite3')
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        self.reopen()
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')

    def test_cleanup_requires_a_cancelled_known_order(self):
        self.seed_hold()
        with self.assertRaises(KeyError):
            cleanup_cancelled(self.station, self.device, 'absent')
        with self.assertRaises(ValueError):
            cleanup_cancelled(self.station, self.device, 'P-order')
        self.assertEqual(self.register().status, 'pending')
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        self.assertEqual(cleanup_cancelled(self.station, None, 'P-order'), 'pending')
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')

    def test_cancelled_order_is_listed_by_number_and_shift(self):
        first, registration = self.seed_hold()
        cancel(self.station, 'P-order')
        self.reopen(device=False)
        view = lookup(self.station, 'P-order')
        self.assertEqual((view.status, view.signed, view.provenance), ('cancelled', None, ()))
        entries = manifest(self.station, self.shift)
        self.assertEqual([(entry.order_id, entry.status, entry.receipt_id, entry.peak_kpa)
                          for entry in entries], [('P-order', 'cancelled', None, None)])
        self.assertEqual(entries[0].pulse, self.p)

    def test_cancelling_one_order_preserves_the_same_pulse_for_another(self):
        first = self.seed_first_segment()
        held = self.register('P-order').order
        other = self.register('P-copy').order
        tail = self.record(self.p, 2, 11)
        self.assertEqual(cancel(self.station, 'P-order').status, 'cancelled')
        self.assertEqual(cleanup_cancelled(self.station, self.device, 'P-order'), 'cleared')
        with self.assertRaises(RetentionNotReadable):
            read_retained_segments(self.device, held.retention_id, self.p)
        self.assertEqual(len(read_retained_segments(self.device, other.retention_id, self.p).segments), 2)
        view = resume(self.station, self.device, 'P-copy')
        self.assert_p_measurement(view)
        self.assertEqual(view.signed.receipt.receipt_id, 'receipt/P-copy')
        self.assertEqual(lookup(self.station, 'P-order').status, 'cancelled')
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)
        late_release = release_retention(self.device, held.retention_id, self.p)
        self.assertEqual(late_release.status, RetentionStatus.RELEASED)
