"""Driver setup flow.

Reconfiguration doubles as the integration-manager backup/restore interface.
`uc-intg-manager` drives it over the Core API without a human present:

1. it starts setup with ``reconfigure=true`` and an empty ``setup_data``
2. it reads the default value of the dropdown whose id is ``choice``
3. it sends back ``{choice, action: "backup", backup_data: "[]"}``
4. it expects a further user-input page carrying a textarea with id
   ``backup_data`` holding the serialised configuration

Renaming those ids, or dropping the ``choice`` dropdown's default value,
silently breaks backups.

The first page must also carry **no input field beyond those three**. The Remote
requires a value for every input-type field on the current page before it will
accept a reply, so an extra text or password field there makes the manager's
reply fail with 400 -- it only ever sends ``choice``, ``action`` and
``backup_data``. Anything else the flow needs is asked for on a follow-up page.
Only ``label`` fields are exempt, because they have no value to supply.

The same rule applies to ``setup_data_schema`` in ``driver.json``: an input
field there makes ``POST /intg/setup`` with an empty ``setup_data`` fail, which
is how the manager starts a backup.
"""

import logging

import ucapi

from activity_import import ActivityImportError, import_activities
from config import Config, HubConfig
from core_api import CoreApi, CoreApiError, create_api_key
from hub import Hub

_LOG = logging.getLogger(__name__)

ACTION_CONFIGURE = "configure"
ACTION_BACKUP = "backup"
ACTION_RESTORE = "restore"
ACTION_IMPORT = "import"
# Name of the API key this driver provisions for itself.
API_KEY_NAME = "uc-intg-harmony"
# Only ever sent back by a result page, whose sole remaining step is to finish.
ACTION_DONE = "done"


class SetupFlow:
    """Handles the driver setup requests from the Remote."""

    def __init__(self, config: Config, on_hub_added, hubs: dict[str, Hub]) -> None:
        self._config = config
        self._on_hub_added = on_hub_added
        # The live registry from driver.py: importing activities needs the
        # connected Hub, not a fresh one built from stored config.
        self._hubs = hubs
        # Follow-up pages carry only their own field, so the first page's
        # selection is remembered here for the rest of the flow.
        self._choice = ""
        self._action = ACTION_CONFIGURE

    async def handle(self, msg: ucapi.SetupDriver) -> ucapi.SetupAction:
        """Dispatch a setup request."""
        if isinstance(msg, ucapi.DriverSetupRequest):
            self._choice = ""
            self._action = ACTION_CONFIGURE
            if msg.reconfigure:
                return self._reconfigure_page()
            # driver.json carries no input field, so the address is collected
            # here instead. See _address_page for why.
            address = msg.setup_data.get("address", "")
            if not address:
                return self._address_page()
            return await self._add_hub(address)
        if isinstance(msg, ucapi.UserDataResponse):
            return await self._on_user_data(msg)
        return ucapi.SetupError()

    async def _on_user_data(self, msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
        values = msg.input_values

        # Only the first page carries choice and action; a follow-up page
        # answers with its own field alone, so fall back to what was picked.
        if "choice" in values:
            self._choice = values["choice"]
        self._action = values.get("action", self._action)

        if self._action == ACTION_BACKUP:
            return self._backup_page()
        if self._action == ACTION_RESTORE:
            return self._restore(values.get("backup_data", ""))
        if self._action == ACTION_DONE:
            return ucapi.SetupComplete()
        if self._action == ACTION_IMPORT:
            return await self._start_import(values)

        address = values.get("address", "")
        if not address:
            return self._address_page()
        return await self._add_hub(address)

    async def _add_hub(self, address: str) -> ucapi.SetupAction:
        address = address.strip()
        if not address:
            return ucapi.SetupError(ucapi.IntegrationSetupError.OTHER)

        hub = Hub("", address, address)
        try:
            await hub.connect()
        except Exception:  # noqa: BLE001
            _LOG.exception("Cannot connect to Harmony hub at %s", address)
            return ucapi.SetupError(ucapi.IntegrationSetupError.CONNECTION_REFUSED)

        self._config.add(HubConfig(hub.identifier, address, hub.name))
        await self._on_hub_added(hub)
        return ucapi.SetupComplete()

    def _restore(self, payload: str) -> ucapi.SetupAction:
        try:
            count = self._config.restore(payload)
        except ValueError:
            _LOG.exception("Cannot restore configuration")
            return ucapi.SetupError(ucapi.IntegrationSetupError.OTHER)

        _LOG.info("Restored %d hub(s) from backup; restart to apply", count)
        return ucapi.SetupComplete()

    async def _import(self, choice: str, api_key: str) -> ucapi.SetupAction:
        """Create a Remote activity per Harmony activity of the chosen hub."""
        hub = self._hubs.get(choice)
        if hub is None:
            return self._message_page(
                {"en": "Import failed"},
                "Select a configured hub before importing its activities.",
            )

        try:
            async with CoreApi(api_key) as api:
                result = await import_activities(hub, api)
        except (ActivityImportError, CoreApiError) as err:
            _LOG.exception("Cannot import activities of hub %s", hub.name)
            return self._message_page({"en": "Import failed"}, str(err))

        return self._message_page({"en": "Activities imported"}, result.summary())

    def _address_page(self) -> ucapi.RequestUserInput:
        """Ask for the hub address on first-time setup.

        `driver.json` deliberately declares only a label in its
        `setup_data_schema`: the Remote demands a value for every input field
        in that schema before it will start a setup process, so an `address`
        text field there makes `POST /intg/setup` with an empty `setup_data`
        fail with 400 -- which is exactly how uc-intg-manager starts a backup.
        """
        return ucapi.RequestUserInput(
            {"en": "Harmony Hub"},
            [
                {
                    "id": "address",
                    "label": {"en": "Hub IP address"},
                    "field": {"text": {"value": ""}},
                }
            ],
        )

    async def _start_import(self, values: dict) -> ucapi.SetupAction:
        """Obtain an API key if needed, then run the import."""
        api_key = self._config.api_key
        if not api_key:
            pin = (values.get("pin") or "").strip()
            if not pin:
                return self._pin_page()
            try:
                api_key = await create_api_key(pin, API_KEY_NAME)
            except CoreApiError as err:
                _LOG.exception("Cannot create an API key")
                return self._message_page({"en": "Import failed"}, str(err))
            self._config.set_api_key(api_key)

        return await self._import(self._choice, api_key)

    def _pin_page(self) -> ucapi.RequestUserInput:
        """Ask for the web-configurator PIN.

        The PIN is used once, to mint an API key, and is never stored -- the
        web-configurator has no screen for creating a key by hand.
        """
        return ucapi.RequestUserInput(
            {"en": "Import activities"},
            [
                {
                    "id": "info",
                    "label": {"en": ""},
                    "field": {
                        "label": {
                            "value": {
                                "en": "Creating activities needs access to this "
                                "Remote. Enter the web-configurator PIN once and "
                                "an API key will be created and stored, so later "
                                "imports will not ask again. The PIN itself is "
                                "not saved."
                            }
                        }
                    },
                },
                {
                    "id": "pin",
                    "label": {"en": "Web-configurator PIN"},
                    "field": {"password": {"value": ""}},
                },
            ],
        )

    def _message_page(self, title: dict, message: str) -> ucapi.RequestUserInput:
        """Final page reporting an outcome; the only step left is to finish.

        The `action` dropdown is what makes this terminal: without it the next
        submit would fall through to the default configure action and try to add
        a hub with an empty address.
        """
        return ucapi.RequestUserInput(
            title,
            [
                {
                    "id": "info",
                    "label": {"en": ""},
                    "field": {"label": {"value": {"en": message}}},
                },
                {
                    "id": "action",
                    "label": {"en": "Finish"},
                    "field": {
                        "dropdown": {
                            "value": ACTION_DONE,
                            "items": [{"id": ACTION_DONE, "label": {"en": "Finish"}}],
                        }
                    },
                },
            ],
        )

    def _reconfigure_page(self) -> ucapi.RequestUserInput:
        """First reconfiguration screen, also the manager's backup entry point."""
        hubs = list(self._config.hubs.values())
        items = [{"id": hub.hub_id, "label": {"en": hub.name}} for hub in hubs]
        items.append({"id": "new", "label": {"en": "Add a new hub"}})

        return ucapi.RequestUserInput(
            {"en": "Harmony Hub"},
            [
                {
                    "id": "choice",
                    "label": {"en": "Hub"},
                    "field": {
                        "dropdown": {
                            "value": hubs[0].hub_id if hubs else "new",
                            "items": items,
                        }
                    },
                },
                {
                    "id": "action",
                    "label": {"en": "Action"},
                    "field": {
                        "dropdown": {
                            "value": ACTION_CONFIGURE,
                            "items": [
                                {
                                    "id": ACTION_CONFIGURE,
                                    "label": {"en": "Add or update a hub"},
                                },
                                {
                                    "id": ACTION_BACKUP,
                                    "label": {"en": "Show configuration backup"},
                                },
                                {
                                    "id": ACTION_RESTORE,
                                    "label": {"en": "Restore from backup"},
                                },
                                {
                                    "id": ACTION_IMPORT,
                                    "label": {"en": "Import activities to the Remote"},
                                },
                            ],
                        }
                    },
                },
                {
                    "id": "backup_data",
                    "label": {"en": "Backup data"},
                    "field": {"textarea": {"value": ""}},
                },
            ],
        )

    def _backup_page(self) -> ucapi.RequestUserInput:
        """Return the serialised configuration in a `backup_data` textarea."""
        return ucapi.RequestUserInput(
            {"en": "Configuration backup"},
            [
                {
                    "id": "backup_data",
                    "label": {"en": "Backup data"},
                    "field": {"textarea": {"value": self._config.dump()}},
                }
            ],
        )
