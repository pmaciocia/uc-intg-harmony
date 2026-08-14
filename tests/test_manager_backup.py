"""Regression tests for uc-intg-manager backup/restore compatibility.

The manager drives our reconfiguration flow unattended and parses the reply by
hard-coded field ids. These tests reproduce its exact steps, so a rename that
would silently disable backups fails here instead.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ucapi  # noqa: E402

from config import Config, HubConfig  # noqa: E402
from setup_flow import SetupFlow  # noqa: E402


def extract_first_choice_id(response):
    """Copy of uc-intg-manager's backup_service._extract_first_choice_id."""
    settings = response.get("require_user_action", {}).get("input", {}).get("settings", [])
    for setting in settings:
        if setting.get("id") == "choice":
            return setting.get("field", {}).get("dropdown", {}).get("value")
    return None


def extract_backup_data(response):
    """Copy of uc-intg-manager's backup_service._extract_backup_data."""
    settings = response.get("require_user_action", {}).get("input", {}).get("settings", [])
    for setting in settings:
        if setting.get("id") == "backup_data":
            return setting.get("field", {}).get("textarea", {}).get("value")
    return None


def as_wire(action):
    """Serialise a SetupAction the way ucapi.api sends it over the wire."""
    if isinstance(action, ucapi.RequestUserInput):
        return {
            "state": "WAIT_USER_ACTION",
            "require_user_action": {
                "input": {"title": action.title, "settings": action.settings}
            },
        }
    if isinstance(action, ucapi.SetupComplete):
        return {"state": "OK"}
    return {"state": "ERROR"}


class ManagerBackupTest(unittest.TestCase):
    """Exercise the flow uc-intg-manager performs to back up our config."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["UC_CONFIG_HOME"] = self._dir.name
        self.config = Config()
        self.config.hubs = {
            "1234567": HubConfig("1234567", "192.168.1.10", "Living Room"),
            "1234568": HubConfig("1234568", "192.168.1.11", "Study"),
        }
        self.config.store()
        self.flow = SetupFlow(self.config, self._noop)

    def tearDown(self):
        self._dir.cleanup()

    @staticmethod
    async def _noop(_hub):
        return None

    def _handle(self, msg):
        return as_wire(asyncio.run(self.flow.handle(msg)))

    def test_reconfigure_exposes_a_choice_dropdown(self):
        response = self._handle(ucapi.DriverSetupRequest(True, {}))
        self.assertEqual(response["state"], "WAIT_USER_ACTION")
        self.assertTrue(extract_first_choice_id(response))

    def test_choice_dropdown_has_a_value_with_no_hubs_configured(self):
        self.config.clear()
        response = self._handle(ucapi.DriverSetupRequest(True, {}))
        self.assertTrue(extract_first_choice_id(response))

    def test_backup_action_returns_the_configuration(self):
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        choice = extract_first_choice_id(start)

        response = self._handle(
            ucapi.UserDataResponse(
                {"choice": choice, "action": "backup", "backup_data": "[]"}
            )
        )
        self.assertEqual(response["state"], "WAIT_USER_ACTION")

        payload = extract_backup_data(response)
        self.assertIsNotNone(payload)
        self.assertEqual(len(json.loads(payload)["hubs"]), 2)

    def test_backup_restores_round_trip(self):
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        payload = extract_backup_data(
            self._handle(
                ucapi.UserDataResponse(
                    {
                        "choice": extract_first_choice_id(start),
                        "action": "backup",
                        "backup_data": "[]",
                    }
                )
            )
        )

        self.config.clear()
        response = self._handle(
            ucapi.UserDataResponse({"action": "restore", "backup_data": payload})
        )

        self.assertEqual(response["state"], "OK")
        self.assertEqual(len(self.config.hubs), 2)
        self.assertEqual(self.config.hubs["1234567"].address, "192.168.1.10")

    def test_restored_config_survives_a_reload_from_disk(self):
        payload = self.config.dump()
        self.config.clear()
        self._handle(
            ucapi.UserDataResponse({"action": "restore", "backup_data": payload})
        )

        reloaded = Config()
        reloaded.load()
        self.assertEqual(len(reloaded.hubs), 2)

    def test_corrupt_backup_is_rejected_without_losing_config(self):
        with self.assertLogs("setup_flow", level="ERROR"):
            response = self._handle(
                ucapi.UserDataResponse({"action": "restore", "backup_data": "not json"})
            )
        self.assertEqual(response["state"], "ERROR")
        self.assertEqual(len(self.config.hubs), 2)

    def test_backup_of_empty_config_is_still_valid_json(self):
        self.config.clear()
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        payload = extract_backup_data(
            self._handle(
                ucapi.UserDataResponse(
                    {
                        "choice": extract_first_choice_id(start),
                        "action": "backup",
                        "backup_data": "[]",
                    }
                )
            )
        )
        self.assertEqual(json.loads(payload), {"hubs": []})


if __name__ == "__main__":
    unittest.main()
