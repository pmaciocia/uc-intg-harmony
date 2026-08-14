"""Shared constants and entity-id helpers."""

HUB = "hub"
DEVICE = "device"

POWER_OFF_ACTIVITY_ID = "-1"
POWER_OFF_SOURCE = "PowerOff"

CMD_SYNC = "sync"

UI_GRID_WIDTH = 4
UI_GRID_HEIGHT = 6


def hub_entity_id(hub_id: str) -> str:
    """Build the media-player entity id for a hub."""
    return f"{hub_id}|{HUB}|{hub_id}"


def device_entity_id(hub_id: str, device_id: str) -> str:
    """Build the remote entity id for a device behind a hub."""
    return f"{hub_id}|{DEVICE}|{device_id}"


def parse_entity_id(entity_id: str) -> tuple[str, str, str]:
    """Split an entity id into (hub_id, kind, target_id)."""
    hub_id, kind, target_id = entity_id.split("|", 2)
    return hub_id, kind, target_id
