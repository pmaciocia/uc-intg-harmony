"""Entity construction tests.

Entity ids must keep the same shape as the original Docker integration so users
migrating from it keep their existing activity and button mappings. The hub and
device ids below are synthetic; only their arrangement is meaningful.
"""

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import media_player  # noqa: E402
import remote  # noqa: E402

from const import UI_GRID_HEIGHT, UI_GRID_WIDTH  # noqa: E402
from hub import Activity, Device, Hub, _commands  # noqa: E402


def ir(command, label):
    """Build a hub controlGroup function entry."""
    return {
        "action": json.dumps({"command": command, "type": "IRCommand"}),
        "name": command,
        "label": label,
    }


HUB_CONFIG = {
    "activity": [
        {"id": "-1", "label": "PowerOff"},
        {"id": "9000001", "label": "Watch TV"},
        {"id": "9000002", "label": "Listen to Radio"},
    ],
    "device": [
        {
            "id": "7654321",
            "label": "Denon AV Receiver",
            "controlGroup": [
                {"name": "Power", "function": [ir("PowerOn", "Power On"),
                                               ir("PowerOff", "Power Off")]},
                {"name": "Volume", "function": [ir("VolumeUp", "Volume Up"),
                                                ir("VolumeDown", "Volume Down")]},
            ],
        },
        {
            "id": "7654322",
            "label": "Set Top Box",
            # 30 commands: more than one 4x6 page holds.
            "controlGroup": [
                {"name": "All", "function": [ir(f"Cmd{n}", f"Command {n}")
                                             for n in range(30)]}
            ],
        },
    ],
}


class FakeHub(Hub):
    """A hub backed by a static config instead of a live connection."""

    def __init__(self, config):
        super().__init__("1234567", "192.168.1.10", "Living Room")
        self._config = config
        self.sent = []

    @property
    def connected(self):
        return True

    @property
    def current_activity(self):
        return Activity("9000001", "Watch TV")

    @property
    def activities(self):
        return [
            Activity(str(a["id"]), a["label"])
            for a in self._config["activity"]
            if str(a["id"]) != "-1"
        ]

    @property
    def devices(self):
        return [
            Device(str(d["id"]), d["label"], _commands(d))
            for d in self._config["device"]
        ]

    async def send_command(self, device_id, command):
        self.sent.append((device_id, command))

    async def start_activity(self, activity_id):
        self.sent.append(("activity", activity_id))

    async def power_off(self):
        self.sent.append(("off", None))


class EntityTest(unittest.TestCase):
    """Verify the entity model matches the original integration."""

    def setUp(self):
        self.hub = FakeHub(HUB_CONFIG)
        self.mp = media_player.create(self.hub)
        self.remotes = [remote.create(self.hub, d) for d in self.hub.devices]

    def test_media_player_id_shape_matches_original(self):
        self.assertEqual(self.mp.id, "1234567|hub|1234567")

    def test_remote_id_shape_matches_original(self):
        self.assertEqual(self.remotes[0].id, "1234567|device|7654321")

    def test_power_off_is_excluded_from_source_list(self):
        sources = self.mp.attributes["source_list"]
        self.assertEqual(sources, ["Watch TV", "Listen to Radio"])

    def test_devices_expose_their_ir_commands(self):
        self.assertEqual(
            self.remotes[0].options["simple_commands"],
            ["PowerOn", "PowerOff", "VolumeUp", "VolumeDown"],
        )

    def test_ui_pages_paginate_and_stay_in_bounds(self):
        pages = self.remotes[1].options["user_interface"]["pages"]
        self.assertEqual(len(pages), 2)
        for page in pages:
            for item in page["items"]:
                self.assertLess(item["location"]["x"], UI_GRID_WIDTH)
                self.assertLess(item["location"]["y"], UI_GRID_HEIGHT)

    def test_every_command_appears_on_a_ui_page(self):
        pages = self.remotes[1].options["user_interface"]["pages"]
        on_pages = {i["command"]["cmd_id"] for p in pages for i in p["items"]}
        self.assertEqual(on_pages, set(self.remotes[1].options["simple_commands"]))

    def test_simple_command_is_sent_to_the_hub(self):
        status = asyncio.run(self.remotes[0].command("VolumeUp", websocket=None))
        self.assertEqual(status, 200)
        self.assertEqual(self.hub.sent, [("7654321", "VolumeUp")])

    def test_send_cmd_honours_repeat(self):
        asyncio.run(
            self.remotes[0].command(
                "send_cmd", {"command": "VolumeUp", "repeat": 3}, websocket=None
            )
        )
        self.assertEqual(len(self.hub.sent), 3)

    def test_unknown_command_is_rejected(self):
        status = asyncio.run(self.remotes[0].command("Nope", websocket=None))
        self.assertEqual(status, 501)

    def test_device_without_power_command_rejects_on(self):
        bare = Device("1", "Bare", [])
        entity = remote.create(self.hub, bare)
        self.assertEqual(asyncio.run(entity.command("on", websocket=None)), 400)

    def test_select_source_starts_the_activity(self):
        status = asyncio.run(
            self.mp.command("select_source", {"source": "Listen to Radio"}, websocket=None)
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.hub.sent, [("activity", "9000002")])

    def test_select_unknown_source_is_rejected(self):
        status = asyncio.run(
            self.mp.command("select_source", {"source": "Nope"}, websocket=None)
        )
        self.assertEqual(status, 400)

    def test_disconnected_hub_reports_unavailable(self):
        class Offline(FakeHub):
            @property
            def connected(self):
                return False

        self.assertEqual(
            media_player.attributes(Offline(HUB_CONFIG))["state"], "UNAVAILABLE"
        )


if __name__ == "__main__":
    unittest.main()
