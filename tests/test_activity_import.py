"""Activity import tests.

The payload shapes asserted here were captured from a live Remote (firmware
2.9.13) during development: an activity is created without sequences, then
patched with them, and command ids are entity-type namespaced.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from activity_import import (  # noqa: E402
    CMD_OFF,
    CMD_SELECT_SOURCE,
    ActivityImportError,
    import_activities,
)
from core_api import CoreApi, CoreApiError  # noqa: E402
from hub import Activity, Hub  # noqa: E402

HUB_ID = "18503271"
MEDIA_PLAYER_ID = f"harmony.main.{HUB_ID}|hub|{HUB_ID}"


class FakeHub(Hub):
    """A hub with a fixed activity list and no aioharmony underneath."""

    def __init__(self, activities, connected=True):
        super().__init__(HUB_ID, "192.168.1.50", "choch-hub")
        self._activities = activities
        self._connected = connected

    @property
    def activities(self):
        return self._activities


class FakeApi:
    """Records calls in place of a real Core API."""

    def __init__(self, existing_activities=(), entities=None, fail_on=()):
        self._existing = [{"name": {"en": name}} for name in existing_activities]
        self._entities = (
            entities
            if entities is not None
            else [{"entity_id": MEDIA_PLAYER_ID, "entity_type": "media_player"}]
        )
        self._fail_on = set(fail_on)
        self.created = []
        self.patched = []

    async def entities(self):
        return self._entities

    async def activities(self):
        return self._existing

    async def create_activity(self, name, entity_ids, icon=None):
        if name in self._fail_on:
            raise CoreApiError("the Remote returned 500: boom", 500)
        self.created.append({"name": name, "entity_ids": entity_ids, "icon": icon})
        return f"uc.main.{name.replace(' ', '-')}"

    async def set_sequences(self, entity_id, entity_ids, on, off):
        self.patched.append(
            {"entity_id": entity_id, "entity_ids": entity_ids, "on": on, "off": off}
        )


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ActivityImportTest(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub(
            [Activity("9000001", "Watch TV"), Activity("9000002", "Listen to Radio")]
        )

    def test_creates_one_activity_per_harmony_activity(self):
        api = FakeApi()
        result = run(import_activities(self.hub, api))
        self.assertEqual(["Watch TV", "Listen to Radio"], result.created)
        self.assertEqual(2, len(api.created))

    def test_create_carries_entity_ids_but_no_sequences(self):
        api = FakeApi()
        run(import_activities(self.hub, api))
        self.assertEqual([MEDIA_PLAYER_ID], api.created[0]["entity_ids"])
        self.assertNotIn("sequences", api.created[0])

    def test_on_sequence_selects_the_activity_as_source(self):
        api = FakeApi()
        run(import_activities(self.hub, api))
        step = api.patched[0]["on"][0]
        self.assertEqual("command", step["type"])
        self.assertEqual(CMD_SELECT_SOURCE, step["command"]["cmd_id"])
        self.assertEqual(MEDIA_PLAYER_ID, step["command"]["entity_id"])
        self.assertEqual({"source": "Watch TV"}, step["command"]["params"])

    def test_off_sequence_powers_the_hub_off(self):
        api = FakeApi()
        run(import_activities(self.hub, api))
        step = api.patched[0]["off"][0]
        self.assertEqual(CMD_OFF, step["command"]["cmd_id"])
        self.assertEqual(MEDIA_PLAYER_ID, step["command"]["entity_id"])

    def test_patch_resends_entity_ids(self):
        # Every entity used by a sequence must also be included in the activity,
        # and a PATCH replaces rather than merges.
        api = FakeApi()
        run(import_activities(self.hub, api))
        self.assertEqual([MEDIA_PLAYER_ID], api.patched[0]["entity_ids"])

    def test_existing_activity_is_skipped_and_never_patched(self):
        api = FakeApi(existing_activities=["Watch TV"])
        result = run(import_activities(self.hub, api))
        self.assertEqual(["Watch TV"], result.skipped)
        self.assertEqual(["Listen to Radio"], result.created)
        self.assertEqual(["Listen to Radio"], [c["name"] for c in api.created])
        self.assertEqual(1, len(api.patched))

    def test_collision_matching_ignores_case(self):
        api = FakeApi(existing_activities=["watch tv"])
        result = run(import_activities(self.hub, api))
        self.assertEqual(["Watch TV"], result.skipped)

    def test_one_failure_does_not_abort_the_rest(self):
        api = FakeApi(fail_on=["Watch TV"])
        result = run(import_activities(self.hub, api))
        self.assertEqual(["Watch TV"], result.failed)
        self.assertEqual(["Listen to Radio"], result.created)

    def test_disconnected_hub_fails_before_any_call(self):
        hub = FakeHub([Activity("1", "Watch TV")], connected=False)
        api = FakeApi()
        with self.assertRaises(ActivityImportError):
            run(import_activities(hub, api))
        self.assertEqual([], api.created)

    def test_missing_media_player_entity_is_reported(self):
        api = FakeApi(entities=[])
        with self.assertRaises(ActivityImportError) as ctx:
            run(import_activities(self.hub, api))
        self.assertIn("media-player", str(ctx.exception))

    def test_media_player_of_another_hub_is_not_used(self):
        api = FakeApi(
            entities=[
                {"entity_id": "harmony.main.999|hub|999", "entity_type": "media_player"}
            ]
        )
        with self.assertRaises(ActivityImportError):
            run(import_activities(self.hub, api))

    def test_non_media_player_with_matching_suffix_is_ignored(self):
        api = FakeApi(
            entities=[
                {"entity_id": MEDIA_PLAYER_ID, "entity_type": "remote"},
                {"entity_id": MEDIA_PLAYER_ID, "entity_type": "media_player"},
            ]
        )
        run(import_activities(self.hub, api))
        self.assertEqual([MEDIA_PLAYER_ID], api.patched[0]["entity_ids"])
        self.assertEqual(
            MEDIA_PLAYER_ID, api.patched[0]["on"][0]["command"]["entity_id"]
        )

    def test_summary_reports_every_outcome(self):
        api = FakeApi(existing_activities=["Watch TV"])
        result = run(import_activities(self.hub, api))
        summary = result.summary()
        self.assertIn("Listen to Radio", summary)
        self.assertIn("Watch TV", summary)
        self.assertIn("Skipped", summary)


class CoreApiPagingTest(unittest.TestCase):
    """The core pages at 100 per request; a second page must not be missed."""

    def _client(self, pages):
        api = CoreApi("key")
        seen = []

        async def fake_request(_method, path, params=None, **_kwargs):
            seen.append(params["page"])
            return pages[params["page"] - 1]

        api._request = fake_request  # noqa: SLF001
        return api, seen

    def test_reads_every_page(self):
        pages = [[{"n": i} for i in range(100)], [{"n": 100}]]
        api, seen = self._client(pages)
        items = run(api.activities())
        self.assertEqual(101, len(items))
        self.assertEqual([1, 2], seen)

    def test_stops_on_a_short_page(self):
        api, seen = self._client([[{"n": 1}]])
        self.assertEqual(1, len(run(api.activities())))
        self.assertEqual([1], seen)

    def test_empty_collection_is_not_an_error(self):
        api, _ = self._client([[]])
        self.assertEqual([], run(api.entities()))


if __name__ == "__main__":
    unittest.main()
