#!/usr/bin/env python3
"""Logitech Harmony Hub integration driver for the Unfolded Circle Remote."""

import asyncio
import logging
import os
import sys

import ucapi

import media_player
import remote
from config import Config
from const import HUB, parse_entity_id
from hub import Events, Hub
from setup_flow import SetupFlow

_LOG = logging.getLogger("driver")

loop = asyncio.new_event_loop()
api = ucapi.IntegrationAPI(loop)
config = Config()
hubs: dict[str, Hub] = {}


async def add_hub(hub: Hub) -> None:
    """Register a hub and track its entities."""
    hubs[hub.identifier] = hub

    hub.events.on(Events.CONNECTED, _on_hub_available)
    hub.events.on(Events.CONFIG_UPDATED, _on_hub_available)
    hub.events.on(Events.DISCONNECTED, _on_hub_state)
    hub.events.on(Events.ACTIVITY_CHANGED, _on_hub_state)

    # A hub configured through the setup flow is already connected, so its
    # CONNECTED event fired before these listeners existed.
    if hub.connected:
        _on_hub_available(hub.identifier)


def _publish_entities(hub: Hub) -> None:
    entities = [media_player.create(hub)]
    entities += [remote.create(hub, device) for device in hub.devices]
    for entity in entities:
        # add() ignores an existing id, so replace to pick up hub config changes
        api.available_entities.remove(entity.id)
        api.available_entities.add(entity)


def _on_hub_state(hub_id: str) -> None:
    hub = hubs[hub_id]
    for entry in api.configured_entities.get_all():
        identifier = entry["entity_id"]
        if not identifier.startswith(f"{hub_id}|"):
            continue
        _, kind, _ = parse_entity_id(identifier)
        attributes = (
            media_player.attributes(hub) if kind == HUB else remote.attributes(hub)
        )
        api.configured_entities.update_attributes(identifier, attributes)


def _on_hub_available(hub_id: str) -> None:
    _publish_entities(hubs[hub_id])
    _on_hub_state(hub_id)


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect(**_kwargs) -> None:
    """Connect to all configured hubs when the Remote connects."""
    for hub in hubs.values():
        if not hub.connected:
            await hub.connect()
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_disconnect(**_kwargs) -> None:
    """Close all hub connections when the Remote disconnects."""
    for hub in hubs.values():
        await hub.disconnect()
    await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe(entity_ids: list[str], **_kwargs) -> None:
    """Push the current state of newly subscribed entities."""
    for entity_id in entity_ids:
        hub_id, kind, _ = parse_entity_id(entity_id)
        hub = hubs.get(hub_id)
        if hub is None:
            continue
        attributes = (
            media_player.attributes(hub) if kind == HUB else remote.attributes(hub)
        )
        api.configured_entities.update_attributes(entity_id, attributes)


async def main() -> None:
    """Load the configuration and start the integration API."""
    logging.basicConfig(level=os.getenv("UC_LOG_LEVEL", "INFO"))

    config.load()
    for stored in config.hubs.values():
        await add_hub(Hub(stored.hub_id, stored.address, stored.name))

    # Only ./bin, ./config and ./data of the installed archive are readable at
    # runtime on the Remote, so the archive-root driver.json cannot be opened
    # from bin/driver. build.sh bundles a copy with --add-data instead, which
    # PyInstaller unpacks into sys._MEIPASS. Outside the bundle the repo copy
    # sits one level above this source dir.
    bundle = getattr(sys, "_MEIPASS", None)
    driver_json = (
        os.path.join(bundle, "driver.json")
        if bundle
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "driver.json")
    )
    setup = SetupFlow(config, add_hub, hubs)
    await api.init(driver_json, setup.handle)


if __name__ == "__main__":
    loop.run_until_complete(main())
    loop.run_forever()
