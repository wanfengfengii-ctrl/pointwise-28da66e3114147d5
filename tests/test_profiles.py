from contextlib import closing
from dataclasses import replace

from capture.resume import resume
from device.segments import list_recorded_segments
from spec.initialize import DEFAULT_PROFILES, initialize_profiles
from spec.profile import ProfileVersion
from spec.versions import ProfileConflict, configure, get_version
from storage import open_database
from tests.support import StationCase


class ProfileTests(StationCase):
    def test_initialize_twice(self):
        user = ProfileVersion('aux-line', 'shop-v1', 2, -20, 50, 3, -4)
        self.assertEqual(configure(self.station, user), user)
        initialize_profiles(self.station)
        self.reopen()
        initialize_profiles(self.station)
        self.assertEqual(get_version(self.station, user.key), user)
        self.assertEqual(get_version(self.station, self.profile.key), self.profile)
        keys = self.station.execute('SELECT line, revision FROM profile_versions').fetchall()
        self.assertCountEqual([tuple(row) for row in keys], [self.profile.key, user.key])

    def test_immutable_version(self):
        self.assertEqual(configure(self.station, self.profile), self.profile)
        with self.assertRaises(ProfileConflict):
            configure(self.station, replace(self.profile, slope=9))
        invalid = (
            replace(self.profile, segment_count=0), replace(self.profile, raw_min=1001),
            replace(self.profile, slope=1.5), replace(self.profile, intercept=True),
            replace(self.profile, line=' '), replace(self.profile, revision=''),
        )
        for profile in invalid:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                configure(self.station, profile)
        self.assertEqual(get_version(self.station, self.profile.key), self.profile)
        self.assertEqual(self.station.execute('SELECT count(*) FROM profile_versions').fetchone()[0], 1)

    def test_initialize_preserves_an_existing_default_key(self):
        customized = replace(DEFAULT_PROFILES[0], slope=5)
        with closing(open_database(self.root / 'preconfigured.sqlite3')) as connection:
            configure(connection, customized)
            initialize_profiles(connection)
            initialize_profiles(connection)
            self.assertEqual(get_version(connection, customized.key), customized)

    def test_record_with_user_version(self):
        profile = ProfileVersion('aux-line', 'shop-v2', 2, -9, 9, 3, -4)
        configure(self.station, profile)
        self.reopen()
        restored = get_version(self.station, profile.key)
        self.trigger(self.p, profile=restored)
        first = self.record(self.p, 1, 2)
        self.assertEqual(list_recorded_segments(self.device, self.p), (first,))
        self.register(profile=restored)
        second = self.record(self.p, 2, -1)
        view = resume(self.station, self.device, 'P-order')
        self.assertEqual(view.signed.profile_key, profile.key)
        self.assertEqual(view.signed.measurement.readings_kpa, (2, -7))
        self.assertEqual(tuple(row.origin for row in view.provenance), (first.origin, second.origin))
