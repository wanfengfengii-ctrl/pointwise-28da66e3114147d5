from spec.profile import ProfileVersion
from storage import transaction


class ProfileConflict(ValueError):
    pass


def ensure_version_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS profile_versions (
            line TEXT NOT NULL,
            revision TEXT NOT NULL,
            segment_count TEXT NOT NULL,
            raw_min TEXT NOT NULL,
            raw_max TEXT NOT NULL,
            slope TEXT NOT NULL,
            intercept TEXT NOT NULL,
            PRIMARY KEY (line, revision)
        )
    ''')


def profile_columns(profile):
    return (
        profile.line, profile.revision, hex(profile.segment_count),
        hex(profile.raw_min), hex(profile.raw_max), hex(profile.slope), hex(profile.intercept)
    )


def profile_from_row(row):
    return ProfileVersion(
        row['line'], row['revision'], int(row['segment_count'], 16),
        int(row['raw_min'], 16), int(row['raw_max'], 16),
        int(row['slope'], 16), int(row['intercept'], 16)
    )


def get_version(connection, key):
    row = connection.execute('''
        SELECT * FROM profile_versions WHERE line = ? AND revision = ?
    ''', key).fetchone()
    return None if row is None else profile_from_row(row)


def insert_missing_version(connection, profile):
    profile.validate()
    connection.execute('''
        INSERT INTO profile_versions
            (line, revision, segment_count, raw_min, raw_max, slope, intercept)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (line, revision) DO NOTHING
    ''', profile_columns(profile))


def configure(connection, profile):
    profile.validate()
    with transaction(connection, write=True):
        ensure_version_store(connection)
        insert_missing_version(connection, profile)
        stored = get_version(connection, profile.key)
        if stored != profile:
            raise ProfileConflict(f'profile {profile.key!r} already has different parameters')
    return stored
