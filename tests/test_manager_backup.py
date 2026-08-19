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
        self.flow = SetupFlow(self.config, self._noop, {})

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

    def test_first_page_has_no_input_field_beyond_the_manager_contract(self):
        """The Remote rejects a reply that omits a value for any input field.

        uc-intg-manager only ever sends choice, action and backup_data, so any
        further text or password field here makes its reply fail with 400.
        Label fields are exempt: they have no value to supply.
        """
        response = self._handle(ucapi.DriverSetupRequest(True, {}))
        settings = response["require_user_action"]["input"]["settings"]

        inputs = {
            s["id"] for s in settings if list(s["field"])[0] != "label"
        }
        self.assertEqual({"choice", "action", "backup_data"}, inputs)

    def test_import_asks_for_the_pin_on_a_second_page(self):
        """No stored key yet, so the driver must ask for the PIN to mint one."""
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        response = self._handle(
            ucapi.UserDataResponse(
                {
                    "choice": extract_first_choice_id(start),
                    "action": "import",
                    "backup_data": "[]",
                }
            )
        )
        settings = response["require_user_action"]["input"]["settings"]
        self.assertIn("pin", [s["id"] for s in settings])
        self.assertNotIn("api_key", [s["id"] for s in settings])

    def test_import_does_not_ask_again_once_a_key_is_stored(self):
        self.config.set_api_key("stored-key")
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        response = self._handle(
            ucapi.UserDataResponse(
                {
                    "choice": extract_first_choice_id(start),
                    "action": "import",
                    "backup_data": "[]",
                }
            )
        )
        settings = response["require_user_action"]["input"]["settings"]
        self.assertNotIn("pin", [s["id"] for s in settings])

    def test_pin_is_never_written_to_disk(self):
        """The PIN mints a key and is then discarded."""
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        self._handle(
            ucapi.UserDataResponse(
                {
                    "choice": extract_first_choice_id(start),
                    "action": "import",
                    "backup_data": "[]",
                }
            )
        )
        with open(os.path.join(self._dir.name, "config.json"), encoding="utf-8") as f:
            stored = f.read()
        self.assertNotIn("pin", stored)

    def test_configure_asks_for_the_address_on_a_second_page(self):
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        response = self._handle(
            ucapi.UserDataResponse(
                {
                    "choice": extract_first_choice_id(start),
                    "action": "configure",
                    "backup_data": "[]",
                }
            )
        )
        settings = response["require_user_action"]["input"]["settings"]
        self.assertIn("address", [s["id"] for s in settings])

    def test_every_dropdown_id_is_a_string(self):
        """The Remote rejects a settings page whose dropdown ids are not strings.

        aioharmony reports the hub id as an int; when that reached the choice
        dropdown unconverted the core silently dropped the whole page, so setup
        never left the SETUP state and backup timed out with no error anywhere.
        """
        self.config.hubs = {"18503271": HubConfig(18503271, "192.168.1.50", "hub")}
        response = self._handle(ucapi.DriverSetupRequest(True, {}))

        for setting in response["require_user_action"]["input"]["settings"]:
            dropdown = setting["field"].get("dropdown")
            if not dropdown:
                continue
            self.assertIsInstance(dropdown["value"], str, setting["id"])
            for item in dropdown["items"]:
                self.assertIsInstance(item["id"], str, setting["id"])

    def test_first_time_setup_asks_for_the_address(self):
        """driver.json declares no input field, so the driver must ask.

        The Remote requires a value for every input field in
        `setup_data_schema` before it will start a setup process, and
        uc-intg-manager starts one with an empty `setup_data`.
        """
        response = self._handle(ucapi.DriverSetupRequest(False, {}))
        self.assertEqual(response["state"], "WAIT_USER_ACTION")
        settings = response["require_user_action"]["input"]["settings"]
        self.assertIn("address", [s["id"] for s in settings])

    def test_driver_json_schema_has_no_input_fields(self):
        """A non-label field there 400s the manager's POST /intg/setup."""
        path = os.path.join(os.path.dirname(__file__), "..", "driver.json")
        with open(path, encoding="utf-8") as file:
            schema = json.load(file)["setup_data_schema"]
        for setting in schema["settings"]:
            self.assertEqual(
                ["label"], list(setting["field"]), f"{setting['id']} is not a label"
            )

    def test_api_key_is_kept_out_of_the_backup(self):
        """The manager stores and replays this blob; a credential must not ride along."""
        self.config.set_api_key("secret-key")
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
        self.assertNotIn("secret-key", payload)
        self.assertNotIn("api_key", json.loads(payload))

    def test_backup_still_works_with_the_import_action_present(self):
        """Adding an action option must not disturb the manager's parsing."""
        start = self._handle(ucapi.DriverSetupRequest(True, {}))
        settings = start["require_user_action"]["input"]["settings"]

        actions = next(s for s in settings if s["id"] == "action")
        ids = [item["id"] for item in actions["field"]["dropdown"]["items"]]
        self.assertIn("import", ids)
        # The manager sends action=backup explicitly, but a changed default
        # would still break anyone relying on it.
        self.assertEqual("configure", actions["field"]["dropdown"]["value"])

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
        self.assertEqual(len(json.loads(payload)["hubs"]), 2)

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
