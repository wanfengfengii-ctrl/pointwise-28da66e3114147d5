from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.publish import receive_and_publish
from capture.cancel import cancel, cleanup_cancelled
from capture.register import initialize_station, register
from capture.resume import resume
from device.pulses import PulseIdentity
from device.readout import read_retained_segments
from device.retention import query_retention, request_retention
from device.segments import SegmentOrigin, record_segment
from device.triggers import initialize_device, trigger_pulse
from shift.lookup import lookup
from shift.manifest import manifest
from spec.versions import configure, get_version
from storage import open_database


def run_recording(station, recorder, sample):
    initialize_station(station)
    initialize_device(recorder)
    profile = get_version(station, tuple(sample['profile']))
    slot = sample['slot']
    recorder_name = sample['recorder']
    observations = []
    for operation, *args in sample['events']:
        if operation == 'trigger':
            trigger_pulse(recorder, PulseIdentity(recorder_name, args[0]), slot, profile)
        elif operation == 'record':
            name, position, raw_code, source = args
            pulse = PulseIdentity(recorder_name, name)
            origin = SegmentOrigin(pulse, position, slot, source)
            record_segment(recorder, pulse, position, raw_code, origin)
        elif operation == 'register':
            order_id, name = args
            register(station, recorder, order_id, PulseIdentity(recorder_name, name),
                     profile.segment_count, profile.key, shift=sample['shift'])
        elif operation == 'resume':
            order_id, = args
            observations.append(resume(station, recorder, order_id))
        else:
            raise ValueError(f'unknown sample event {operation!r}')
    configure(station, replace(profile, revision='v2', slope=4))
    return observations


def summarize_signed(view):
    result = view.signed
    return {
        'order_id': view.order_id,
        'profile': list(result.profile_key),
        'readings_kpa': list(result.measurement.readings_kpa),
        'peak_kpa': result.measurement.peak_kpa,
        'positions': [row.position for row in view.provenance],
        'origin_pulses': [row.origin.pulse.pulse for row in view.provenance],
        'sources': [row.origin.source for row in view.provenance],
        'receipt': result.receipt.receipt_id,
        'reconciled': view.reconciliation.consistent,
    }


def cancellation_order(station, recorder, order_id, pulse, profile, *, lose_reply=False):
    return register(
        station, recorder, order_id, pulse, profile.segment_count, profile.key,
        shift='night-A', lose_reply=lose_reply
    )


def run_cancellation_cancel_first(station_path, device_path):
    with closing(open_database(station_path)) as station, closing(open_database(device_path)) as recorder:
        initialize_station(station)
        initialize_device(recorder)
        profile = get_version(station, ('fan-line', 'v1'))
        pulse = PulseIdentity('recorder-1', 'P')
        trigger_pulse(recorder, pulse, 'slot-1', profile)
        first_origin = SegmentOrigin(pulse, 1, 'slot-1', 'P/segment-1')
        tail_origin = SegmentOrigin(pulse, 2, 'slot-1', 'P/segment-2')
        first = record_segment(recorder, pulse, 1, 7, first_origin)

        # A registers offline; the station fixes A's number and shift.
        a = cancellation_order(station, None, 'A', pulse, profile).order
        # B registers against the same physical pulse and gains its own hold.
        cancellation_order(station, recorder, 'B', pulse, profile)
        assert receive_and_publish(station, 'A', [first]).missing_positions == (2,)
        # The device may already hold for A even though the answer was lost.
        lost = cancellation_order(station, recorder, 'A', pulse, profile, lose_reply=True)
        assert lost.status == 'pending_confirmation'
        assert query_retention(recorder, a.retention_id).status.value == 'held'

        # Another session reads the real tail from A's hold before cancelling.
        tail = record_segment(recorder, pulse, 2, 11, tail_origin)
        with closing(open_database(device_path)) as peer:
            late_tail = read_retained_segments(peer, a.retention_id, pulse).segments[1]
        assert late_tail == tail

        verdict = cancel(station, 'A')
        delayed = receive_and_publish(station, 'A', [late_tail])
        replay = cancellation_order(station, recorder, 'A', pulse, profile)
        retained_a = query_retention(recorder, a.retention_id).status.value
        cleanup = cleanup_cancelled(station, recorder, 'A')
        late_a = request_retention(recorder, a.retention_id, pulse)
        resumed_b = resume(station, recorder, 'B')

    with closing(open_database(station_path)) as station:
        offline_a = lookup(station, 'A')
        offline_b = lookup(station, 'B')
        shift_entries = manifest(station, 'night-A')
    return {
        'cancel_verdict': verdict.status,
        'delayed_publication_signed': delayed.signed is not None,
        'replay_status': replay.status,
        'resumed_b': resumed_b.status,
        'a_retention_before_cleanup': retained_a,
        'cleanup': cleanup,
        'a_late_request_status': late_a.status.value,
        'offline_a': {'status': offline_a.status, 'signed': offline_a.signed,
                      'provenance': offline_a.provenance},
        'offline_b': {
            'status': offline_b.status,
            'readings_kpa': list(offline_b.signed.measurement.readings_kpa),
            'peak_kpa': offline_b.signed.measurement.peak_kpa,
            'receipt': offline_b.signed.receipt.receipt_id,
            'sources': [row.origin.source for row in offline_b.provenance],
        },
        'shift': [[entry.order_id, entry.status, entry.receipt_id, entry.peak_kpa]
                  for entry in shift_entries],
    }


def run_cancellation_sign_first(station_path, device_path):
    with closing(open_database(station_path)) as station, closing(open_database(device_path)) as recorder:
        initialize_station(station)
        initialize_device(recorder)
        profile = get_version(station, ('fan-line', 'v1'))
        pulse = PulseIdentity('recorder-1', 'P')
        trigger_pulse(recorder, pulse, 'slot-1', profile)
        origin_one = SegmentOrigin(pulse, 1, 'slot-1', 'P/segment-1')
        origin_two = SegmentOrigin(pulse, 2, 'slot-1', 'P/segment-2')
        first = record_segment(recorder, pulse, 1, 7, origin_one)
        cancellation_order(station, recorder, 'A', pulse, profile)
        signed = receive_and_publish(station, 'A', [
            first, record_segment(recorder, pulse, 2, 11, origin_two)
        ]).signed
        verdict = cancel(station, 'A', recorder)
    return {
        'signed_before_cancel': signed is not None,
        'cancel_verdict': verdict.status,
        'same_signed_artifact': verdict.signed == signed,
        'receipt': None if verdict.signed is None else verdict.signed.receipt.receipt_id,
    }


def main():
    path = Path(__file__).resolve().parent / 'example' / 'day.json'
    sample = json.loads(path.read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        station_path = root / 'station.sqlite3'
        with closing(open_database(station_path)) as station, closing(open_database(root / 'device.sqlite3')) as recorder:
            live = run_recording(station, recorder, sample)
        with closing(open_database(station_path)) as station:
            offline = [lookup(station, order_id) for order_id in ('order-P', 'order-Q')]
            entries = manifest(station, sample['shift'])
        cancel_first = run_cancellation_cancel_first(
            root / 'cancel-first-station.sqlite3', root / 'cancel-first-device.sqlite3'
        )
        sign_first = run_cancellation_sign_first(
            root / 'sign-first-station.sqlite3', root / 'sign-first-device.sqlite3'
        )

    actual = {
        'resume_statuses': [view.status for view in live],
        'signed': [summarize_signed(view) for view in live if view.status == 'signed'],
    }
    expected = {
        'resume_statuses': ['pending', 'signed', 'signed', 'pending'],
        'signed': [
            {'order_id': 'order-P', 'profile': ['fan-line', 'v1'], 'readings_kpa': [7, 11],
             'peak_kpa': 11, 'positions': [1, 2], 'origin_pulses': ['P', 'P'],
             'sources': ['P/segment-1', 'P/segment-2'], 'receipt': 'receipt/order-P', 'reconciled': True},
            {'order_id': 'order-Q', 'profile': ['fan-line', 'v1'], 'readings_kpa': [13, 29],
             'peak_kpa': 29, 'positions': [1, 2], 'origin_pulses': ['Q', 'Q'],
             'sources': ['Q/segment-1', 'Q/segment-2'], 'receipt': 'receipt/order-Q', 'reconciled': True},
        ],
    }
    downstream_actual = {
        'offline': [summarize_signed(view) for view in offline],
        'manifest': [[item.order_id, item.status, item.receipt_id] for item in entries],
    }
    downstream_expected = {
        'offline': expected['signed'],
        'manifest': [
            ['order-P', 'signed', 'receipt/order-P'],
            ['order-Q', 'signed', 'receipt/order-Q'],
            ['order-R', 'pending', None],
        ],
    }
    cancellation_expected = {
        'cancel_first': {
            'cancel_verdict': 'cancelled',
            'delayed_publication_signed': False,
            'replay_status': 'cancelled',
            'resumed_b': 'signed',
            'a_retention_before_cleanup': 'held',
            'cleanup': 'cleared',
            'a_late_request_status': 'released',
            'offline_a': {'status': 'cancelled', 'signed': None, 'provenance': ()},
            'offline_b': {
                'status': 'signed', 'readings_kpa': [7, 11], 'peak_kpa': 11,
                'receipt': 'receipt/B', 'sources': ['P/segment-1', 'P/segment-2'],
            },
            'shift': [['A', 'cancelled', None, None], ['B', 'signed', 'receipt/B', 11]],
        },
        'sign_first': {
            'signed_before_cancel': True,
            'cancel_verdict': 'signed',
            'same_signed_artifact': True,
            'receipt': 'receipt/A',
        },
    }
    cancellation_actual = {'cancel_first': cancel_first, 'sign_first': sign_first}
    if (actual != expected or downstream_actual != downstream_expected
            or cancellation_actual != cancellation_expected):
        raise AssertionError(
            f'pressure handoff, cancellation or offline review diverged: '
            f'{actual!r}, {downstream_actual!r}, {cancellation_actual!r}'
        )
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'slot_reuse', 'input': sample, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': [
            'device/triggers.py', 'device/segments.py', 'device/retention.py',
            'capture/register.py', 'capture/resume.py', 'capture/cancel.py',
            'archive/publish.py', 'archive/measurement.py', 'shift/lookup.py',
            'shift/manifest.py',
        ],
        'oracle': 'Identity calibration. Hand-order each named pulse by segment number, take its larger reading, and match each source to the recording event. R has only one segment.',
    }, ensure_ascii=False, sort_keys=True))
    print('POINTWISE_CANCELLATION ' + json.dumps({
        'case_id': 'cancel_vs_sign', 'expected': cancellation_expected,
        'actual': cancellation_actual,
        'oracle': 'Cancel first wins: A never signs, its original request clears lastingly, and B still signs [7, 11] from its own hold. Sign first wins: cancel returns the same receipt/A artifact.',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
