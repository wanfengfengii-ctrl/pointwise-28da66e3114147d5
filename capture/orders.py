from dataclasses import dataclass

from device.pulses import PulseIdentity


@dataclass(frozen=True)
class CaptureOrder:
    order_id: str
    pulse: PulseIdentity
    segment_count: int
    profile_key: tuple[str, str]
    shift: str
    retention_id: str
    custody: str = 'pending'
    received_count: int = 0

    @property
    def binding(self):
        return self.pulse, self.segment_count, self.profile_key


def validate_order_id(order_id):
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError('a nonblank order number is required')


def validate_shift(shift):
    if not isinstance(shift, str) or not shift.strip() or shift != shift.strip():
        raise ValueError('shift must be a nonblank name without surrounding whitespace')


def ensure_order_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS capture_orders (
            order_id TEXT PRIMARY KEY,
            recorder TEXT NOT NULL,
            pulse TEXT NOT NULL,
            segment_count TEXT NOT NULL,
            line TEXT NOT NULL,
            revision TEXT NOT NULL,
            shift TEXT NOT NULL,
            retention_id TEXT NOT NULL UNIQUE,
            custody TEXT NOT NULL CHECK (custody IN ('pending', 'held', 'unavailable', 'released')),
            received_count TEXT NOT NULL,
            FOREIGN KEY (line, revision) REFERENCES profile_versions(line, revision)
        )
    ''')
    connection.execute('''
        CREATE INDEX IF NOT EXISTS orders_by_shift ON capture_orders (shift, order_id)
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS cancelled_orders (
            order_id TEXT PRIMARY KEY REFERENCES capture_orders(order_id)
        )
    ''')


def _order_from_row(row):
    return CaptureOrder(
        row['order_id'], PulseIdentity(row['recorder'], row['pulse']),
        int(row['segment_count'], 16), (row['line'], row['revision']), row['shift'],
        row['retention_id'], row['custody'], int(row['received_count'], 16)
    )


def get_order(connection, order_id):
    validate_order_id(order_id)
    row = connection.execute('''
        SELECT * FROM capture_orders WHERE order_id = ?
    ''', (order_id,)).fetchone()
    return None if row is None else _order_from_row(row)


def save_new_order(connection, order):
    connection.execute('''
        INSERT INTO capture_orders
            (order_id, recorder, pulse, segment_count, line, revision,
             shift, retention_id, custody, received_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        order.order_id, *order.pulse.key, hex(order.segment_count), *order.profile_key,
        order.shift, order.retention_id, order.custody, hex(order.received_count)
    ))
    return order


def update_custody(connection, order_id, custody):
    # A delayed observation of a hold can arrive after another session releases it.
    predecessors = {
        'held': ('pending', 'held'),
        'unavailable': ('pending', 'unavailable'),
        'released': ('pending', 'held', 'released'),
    }[custody]
    placeholders = ', '.join('?' for _ in predecessors)
    connection.execute(f'''
        UPDATE capture_orders SET custody = ?
        WHERE order_id = ? AND custody IN ({placeholders})
    ''', (custody, order_id, *predecessors))
    return get_order(connection, order_id)


def save_handoff_progress(connection, order_id, received_count):
    connection.execute('''
        UPDATE capture_orders SET received_count = ? WHERE order_id = ?
    ''', (hex(received_count), order_id))


def list_orders_for_shift(connection, shift):
    validate_shift(shift)
    rows = connection.execute('''
        SELECT * FROM capture_orders WHERE shift = ? ORDER BY order_id COLLATE BINARY
    ''', (shift,))
    return tuple(_order_from_row(row) for row in rows)


def is_cancelled(connection, order_id):
    return connection.execute('''
        SELECT 1 FROM cancelled_orders WHERE order_id = ?
    ''', (order_id,)).fetchone() is not None


def mark_cancelled(connection, order_id):
    connection.execute('''
        INSERT INTO cancelled_orders (order_id) VALUES (?) ON CONFLICT (order_id) DO NOTHING
    ''', (order_id,))
