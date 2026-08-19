"""Persisted driver configuration."""

import json
import logging
import os
from dataclasses import asdict, dataclass

_LOG = logging.getLogger(__name__)

_FILENAME = "config.json"


@dataclass
class HubConfig:
    """A configured Harmony hub."""

    hub_id: str
    address: str
    name: str

    def __post_init__(self) -> None:
        # Older versions stored the id as the int aioharmony reports. The
        # Remote rejects non-string dropdown ids, so heal it on load.
        self.hub_id = str(self.hub_id)


class Config:
    """Load and store the list of configured hubs."""

    def __init__(self) -> None:
        self._path = os.path.join(os.getenv("UC_CONFIG_HOME", "./"), _FILENAME)
        self.hubs: dict[str, HubConfig] = {}
        self.api_key: str | None = None

    def load(self) -> None:
        """Read the configuration from disk, ignoring a missing file."""
        try:
            with open(self._path, encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return

        self.hubs = {hub["hub_id"]: HubConfig(**hub) for hub in data.get("hubs", [])}
        self.api_key = data.get("api_key")

    def store(self) -> None:
        """Write the configuration to disk."""
        data = {"hubs": [asdict(hub) for hub in self.hubs.values()]}
        if self.api_key:
            data["api_key"] = self.api_key
        with open(self._path, "w", encoding="utf-8") as file:
            json.dump(data, file)

    def set_api_key(self, api_key: str) -> None:
        """Store the Core-API key used to create activities."""
        self.api_key = api_key
        self.store()

    def add(self, hub: HubConfig) -> None:
        """Add or replace a hub and persist."""
        self.hubs[hub.hub_id] = hub
        self.store()

    def clear(self) -> None:
        """Drop all hubs and persist."""
        self.hubs = {}
        self.store()

    def dump(self) -> str:
        """Serialise the configuration for an integration-manager backup.

        Deliberately excludes ``api_key``: uc-intg-manager stores this blob and
        replays it on restore, and a credential has no business travelling in a
        backup. A restored install asks for the key again on first import.
        """
        return json.dumps({"hubs": [asdict(hub) for hub in self.hubs.values()]})

    def restore(self, payload: str) -> int:
        """Replace the configuration from a backup payload and persist.

        :return: number of hubs restored
        :raises ValueError: if the payload is not a valid backup
        """
        try:
            data = json.loads(payload)
            hubs = [HubConfig(**hub) for hub in data["hubs"]]
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"invalid backup payload: {err}") from err

        self.hubs = {hub.hub_id: hub for hub in hubs}
        self.store()
        return len(self.hubs)
