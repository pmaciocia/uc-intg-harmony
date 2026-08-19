"""Connection to a single Logitech Harmony hub."""

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum

from aioharmony.const import WEBSOCKETS, ClientCallbackType, SendCommandDevice
from aioharmony.harmonyapi import HarmonyAPI
from pyee.asyncio import AsyncIOEventEmitter

from const import POWER_OFF_ACTIVITY_ID

_LOG = logging.getLogger(__name__)

_RECONNECT_DELAY = 10


class Events(StrEnum):
    """Events emitted by a Hub."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ACTIVITY_CHANGED = "activity_changed"
    CONFIG_UPDATED = "config_updated"


@dataclass
class Command:
    """A single IR command of a device."""

    name: str
    label: str


@dataclass
class Device:
    """A device configured on the hub."""

    identifier: str
    name: str
    commands: list[Command]


@dataclass
class Activity:
    """An activity configured on the hub."""

    identifier: str
    name: str


class Hub:
    """Wraps aioharmony and exposes the hub as devices and activities."""

    def __init__(self, hub_id: str, address: str, name: str) -> None:
        # Always a string: aioharmony reports the hub id as an int, and the
        # Remote rejects a settings page whose dropdown ids are not strings.
        self.identifier = str(hub_id) if hub_id else ""
        self.address = address
        self.name = name
        self.events = AsyncIOEventEmitter()
        self.last_activity_id: str | None = None
        self._api: HarmonyAPI | None = None
        self._connected = False
        self._reconnect_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        """Whether the hub connection is currently established.

        Tracked separately from ``_api``: the client object outlives a dropped
        connection while the reconnect loop retries.
        """
        return self._connected

    @property
    def current_activity(self) -> Activity | None:
        """The activity currently running, or None when powered off."""
        if self._api is None:
            return None
        activity_id, activity_name = self._api.current_activity
        if str(activity_id) == POWER_OFF_ACTIVITY_ID:
            return None
        return Activity(str(activity_id), activity_name)

    @property
    def activities(self) -> list[Activity]:
        """All activities configured on the hub, excluding PowerOff."""
        if self._api is None:
            return []
        return [
            Activity(str(activity["id"]), activity["label"])
            for activity in self._api.config.get("activity", [])
            if str(activity["id"]) != POWER_OFF_ACTIVITY_ID
        ]

    @property
    def devices(self) -> list[Device]:
        """All devices configured on the hub, with their IR commands."""
        if self._api is None:
            return []
        return [
            Device(str(device["id"]), device["label"], _commands(device))
            for device in self._api.config.get("device", [])
        ]

    async def connect(self) -> None:
        """Connect to the hub and start emitting events."""
        if self._connected:
            return

        if self._api is not None:
            # Client exists from an earlier session: just re-establish the link.
            await self._api.connect()
            self._connected = True
            self.events.emit(Events.CONNECTED, self.identifier)
            return

        api = HarmonyAPI(
            ip_address=self.address,
            # Pinning the protocol keeps slixmpp out of the runtime, which the
            # PyInstaller build then excludes to stay inside the memory budget.
            protocol=WEBSOCKETS,
            callbacks=ClientCallbackType(
                connect=self._on_connect,
                disconnect=self._on_disconnect,
                new_activity_starting=None,
                new_activity=self._on_activity,
                config_updated=self._on_config,
            ),
        )
        await api.connect()
        self._api = api
        self._connected = True
        self.name = api.name or self.name
        if not self.identifier:
            self.identifier = str(api.hub_id)
        self.events.emit(Events.CONNECTED, self.identifier)

    async def disconnect(self) -> None:
        """Close the hub connection and stop reconnecting."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._connected = False
        if self._api is not None:
            await self._api.close()
            self._api = None

    async def start_activity(self, activity_id: str) -> None:
        """Start an activity on the hub."""
        await self._api.start_activity(activity_id)

    async def power_off(self) -> None:
        """Stop the running activity."""
        await self._api.power_off()

    async def send_command(self, device_id: str, command: str) -> None:
        """Send a single IR command to a device."""
        await self._api.send_commands(
            SendCommandDevice(device=int(device_id), command=command, delay=0)
        )

    async def sync(self) -> None:
        """Pull the latest configuration from the hub."""
        await self._api.sync()

    def _on_connect(self, _payload) -> None:
        self._connected = True
        self.events.emit(Events.CONNECTED, self.identifier)

    def _on_disconnect(self, _payload) -> None:
        self._connected = False
        self.events.emit(Events.DISCONNECTED, self.identifier)
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect())

    def _on_activity(self, _payload) -> None:
        activity = self.current_activity
        if activity is not None:
            self.last_activity_id = activity.identifier
        self.events.emit(Events.ACTIVITY_CHANGED, self.identifier)

    def _on_config(self, _payload) -> None:
        self.events.emit(Events.CONFIG_UPDATED, self.identifier)

    async def _reconnect(self) -> None:
        while True:
            await asyncio.sleep(_RECONNECT_DELAY)
            try:
                await self._api.connect()
                self._connected = True
                self._reconnect_task = None
                self.events.emit(Events.CONNECTED, self.identifier)
                return
            except Exception:  # noqa: BLE001
                _LOG.debug("%s: reconnect failed, retrying", self.identifier)


def _commands(device: dict) -> list[Command]:
    """Extract the IR command list from a hub device definition."""
    commands = []
    for group in device.get("controlGroup", []):
        for function in group.get("function", []):
            action = json.loads(function["action"])
            commands.append(Command(action["command"], function["label"]))
    return commands
