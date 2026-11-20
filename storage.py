from contextlib import contextmanager
import sqlite3


def open_database(path):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def require_idle(connection):
    if connection.in_transaction:
        raise RuntimeError('operation requires an idle database connection')


@contextmanager
def transaction(connection, *, write=False):
    require_idle(connection)
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
