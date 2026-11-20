"""Observe the original station workflow and provide event controls for tests."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from archive.publish import PublicationInterrupted, receive_and_publish
from capture.register import initialize_station, register
from capture.resume import resume
from device.pulses import PulseIdentity
from device.readout import read_retained_segments
from device.retention import RetentionReplyLost, query_retention, release_retention, request_retention
from device.segments import SegmentOrigin, record_segment
from device.triggers import initialize_device, trigger_pulse
from shift.lookup import lookup
from shift.manifest import manifest
from spec.versions import get_version
from storage import open_database


class Pause:
    def __init__(self):
        self.reached = Event()
        self.proceed = Event()

    def before(self, operation, /, *args, **kwargs):
        self.reached.set()
        if not self.proceed.wait(10):
            raise TimeoutError("paused call was not resumed")
        return operation(*args, **kwargs)


def lose_reply_after(operation, /, *args, **kwargs):
    operation(*args, **kwargs)
    raise RetentionReplyLost("test dropped response after device operation")


def baseline_probe(station_path, device_path, *, connect=open_database):
    with closing(connect(station_path)) as station, closing(connect(device_path)) as device:
        initialize_station(station)
        initialize_device(device)
        profile = get_version(station, ("fan-line", "v1"))
        pulse = PulseIdentity("recorder-1", "P")
        trigger_pulse(device, pulse, "slot-1", profile)
        origins = tuple(SegmentOrigin(pulse, pos, "slot-1", f"P/segment-{pos}") for pos in (1, 2))
        first = record_segment(device, pulse, 1, 7, origins[0])

        def register_order(st, dev, order_id, **options):
            return register(st, dev, order_id, pulse, 2, profile.key, shift="night-A", **options)

        a = register_order(station, None, "A").order
        b = register_order(station, device, "B").order
        assert receive_and_publish(station, "A", [first]).missing_positions == (2,)
        pause = Pause()

        def paused_request(*args, **kwargs):
            return pause.before(request_retention, *args, **kwargs)

        def retry_a():
            with closing(connect(station_path)) as st, closing(connect(device_path)) as dev:
                return register_order(st, dev, "A", lose_reply=True)

        module = importlib.import_module("capture.register")
        with patch.object(module, "request_retention", paused_request), ThreadPoolExecutor(1) as worker:
            retry = worker.submit(retry_a)
            try:
                if not pause.reached.wait(10):
                    raise TimeoutError("retention call did not reach the pause")
                assert query_retention(device, a.retention_id) is None
                assert release_retention(device, a.retention_id, pulse) is None
            finally:
                pause.proceed.set()
            assert retry.result(timeout=10).status == "pending_confirmation"

        assert query_retention(device, a.retention_id).status.value == "held"
        record_segment(device, pulse, 2, 11, origins[1])
        with closing(connect(device_path)) as peer:
            tail = read_retained_segments(peer, a.retention_id, pulse).segments[1]
        assert tail.position == 2 and tail.raw_code == 11 and tail.origin == origins[1]
        assert register_order(station, device, "A").order.custody == "held"
        with closing(connect(station_path)) as receiver:
            try:
                receive_and_publish(receiver, "A", [tail], interrupt_publication=True)
            except PublicationInterrupted:
                assert lookup(station, "A").signed is None
            else:
                raise AssertionError("expected interrupted publication")
            assert receive_and_publish(receiver, "A", [tail]).signed.receipt.receipt_id == "receipt/A"
        assert resume(station, device, "B").signed.receipt.receipt_id == "receipt/B"
        assert query_retention(device, a.retention_id).status.value == "held"
        assert query_retention(device, b.retention_id).status.value == "released"
        try:
            lose_reply_after(release_retention, device, a.retention_id, pulse)
        except RetentionReplyLost:
            pass

    with closing(connect(station_path)) as station:
        view = lookup(station, "B")
        assert tuple(row.origin for row in view.provenance) == origins
        assert view.signed.measurement.readings_kpa == (7, 11)
        assert view.signed.measurement.peak_kpa == 11
        receipts = [entry.receipt_id for entry in manifest(station, "night-A")]
        assert receipts == ["receipt/A", "receipt/B"]
    with closing(connect(device_path)) as device:
        assert query_retention(device, a.retention_id).status.value == "released"
    return {"absent_release": None, "late_a_hold": "held", "lost_release_reply": "released",
            "receipts_without_cancellation": receipts, "b_readings_kpa": [7, 11], "b_peak_kpa": 11}


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        report = baseline_probe(root / "station.sqlite3", root / "device.sqlite3")
    print("V1_CUSTODY_OBSERVATIONS " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
