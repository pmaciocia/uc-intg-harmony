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
"""

import logging

import ucapi

from config import Config, HubConfig
from hub import Hub

_LOG = logging.getLogger(__name__)

ACTION_CONFIGURE = "configure"
ACTION_BACKUP = "backup"
ACTION_RESTORE = "restore"


class SetupFlow:
    """Handles the driver setup requests from the Remote."""

    def __init__(self, config: Config, on_hub_added) -> None:
        self._config = config
        self._on_hub_added = on_hub_added

    async def handle(self, msg: ucapi.SetupDriver) -> ucapi.SetupAction:
        """Dispatch a setup request."""
        if isinstance(msg, ucapi.DriverSetupRequest):
            if msg.reconfigure:
                return self._reconfigure_page()
            return await self._add_hub(msg.setup_data.get("address", ""))
        if isinstance(msg, ucapi.UserDataResponse):
            return await self._on_user_data(msg)
        return ucapi.SetupError()

    async def _on_user_data(self, msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
        values = msg.input_values
        action = values.get("action", ACTION_CONFIGURE)

        if action == ACTION_BACKUP:
            return self._backup_page()
        if action == ACTION_RESTORE:
            return self._restore(values.get("backup_data", ""))
        return await self._add_hub(values.get("address", ""))

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
                            ],
                        }
                    },
                },
                {
                    "id": "address",
                    "label": {"en": "Hub IP address"},
                    "field": {"text": {"value": ""}},
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
