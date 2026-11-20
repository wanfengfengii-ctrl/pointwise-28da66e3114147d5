from spec.profile import ProfileVersion
from spec.versions import ensure_version_store, insert_missing_version
from storage import transaction


DEFAULT_PROFILES = (
    ProfileVersion('fan-line', 'v1', 2, -1000, 1000, 1, 0),
)


def initialize_profiles(connection):
    with transaction(connection, write=True):
        ensure_version_store(connection)
        for profile in DEFAULT_PROFILES:
            insert_missing_version(connection, profile)
