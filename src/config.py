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


class Config:
    """Load and store the list of configured hubs."""

    def __init__(self) -> None:
        self._path = os.path.join(os.getenv("UC_CONFIG_HOME", "./"), _FILENAME)
        self.hubs: dict[str, HubConfig] = {}

    def load(self) -> None:
        """Read the configuration from disk, ignoring a missing file."""
        try:
            with open(self._path, encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return

        self.hubs = {hub["hub_id"]: HubConfig(**hub) for hub in data.get("hubs", [])}

    def store(self) -> None:
        """Write the configuration to disk."""
        with open(self._path, "w", encoding="utf-8") as file:
            json.dump({"hubs": [asdict(hub) for hub in self.hubs.values()]}, file)

    def add(self, hub: HubConfig) -> None:
        """Add or replace a hub and persist."""
        self.hubs[hub.hub_id] = hub
        self.store()

    def clear(self) -> None:
        """Drop all hubs and persist."""
        self.hubs = {}
        self.store()

    def dump(self) -> str:
        """Serialise the configuration for an integration-manager backup."""
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
