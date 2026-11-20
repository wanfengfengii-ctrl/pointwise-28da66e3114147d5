from device.pulses import (
    ensure_pulse_store, get_active_pulse, get_pulse, remember_pulse, retire_pulse,
    set_active_pulse,
)
from device.retention import ensure_retention_store, is_pulse_retained
from device.segments import ensure_segment_store
from storage import transaction


def initialize_device(connection):
    with transaction(connection, write=True):
        ensure_pulse_store(connection)
        ensure_retention_store(connection)
        ensure_segment_store(connection)


def trigger_pulse(connection, pulse, slot, profile):
    pulse.validate()
    profile.validate()
    if not isinstance(slot, str) or not slot.strip():
        raise ValueError('an actual device slot is required')
    with transaction(connection, write=True):
        if get_pulse(connection, pulse) is not None:
            raise ValueError('a physical pulse identity can only be triggered once')
        previous = get_active_pulse(connection)
        if previous is not None and not is_pulse_retained(connection, previous.identity):
            retire_pulse(connection, previous.identity)
        physical = remember_pulse(connection, pulse, slot, profile)
        set_active_pulse(connection, pulse)
        return physical
