from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.register import initialize_station, register
from capture.resume import resume
from device.pulses import PulseIdentity
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
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'pressure handoff or offline review diverged: {actual!r}, {downstream_actual!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'slot_reuse', 'input': sample, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': [
            'device/triggers.py', 'device/segments.py', 'device/retention.py',
            'capture/register.py', 'capture/resume.py', 'archive/publish.py',
            'archive/measurement.py', 'shift/lookup.py', 'shift/manifest.py',
        ],
        'oracle': 'Identity calibration. Hand-order each named pulse by segment number, take its larger reading, and match each source to the recording event. R has only one segment.',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
