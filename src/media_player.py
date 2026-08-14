"""Media-player entity representing a Harmony hub."""

import logging
from typing import Any

from ucapi import MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, States

from const import CMD_SYNC, hub_entity_id
from hub import Hub

_LOG = logging.getLogger(__name__)

_FEATURES = [
    Features.ON_OFF,
    Features.SELECT_SOURCE,
    Features.MEDIA_TITLE,
]


def create(hub: Hub) -> MediaPlayer:
    """Build the media-player entity for a hub."""
    return MediaPlayer(
        hub_entity_id(hub.identifier),
        {"en": hub.name},
        _FEATURES,
        attributes(hub),
        device_class=DeviceClasses.SET_TOP_BOX,
        options={"simple_commands": [CMD_SYNC]},
        cmd_handler=_handler(hub),
    )


def attributes(hub: Hub) -> dict[str, Any]:
    """Build the current attribute state of a hub."""
    if not hub.connected:
        return {Attributes.STATE: States.UNAVAILABLE}

    activity = hub.current_activity
    return {
        Attributes.STATE: States.ON if activity else States.OFF,
        Attributes.SOURCE: activity.name if activity else "",
        Attributes.SOURCE_LIST: [item.name for item in hub.activities],
        Attributes.MEDIA_TITLE: activity.name if activity else "",
    }


def _handler(hub: Hub):
    # The parameter must be named `websocket`: ucapi inspects the handler
    # signature for that name to decide how to invoke it.
    async def handle(
        entity: MediaPlayer, cmd_id: str, params: dict[str, Any] | None, websocket=None
    ) -> StatusCodes:
        _LOG.debug("%s: %s %s", entity.id, cmd_id, params)

        if cmd_id == Commands.OFF:
            await hub.power_off()
        elif cmd_id == Commands.ON:
            activity_id = hub.last_activity_id
            if activity_id is None:
                return StatusCodes.BAD_REQUEST
            await hub.start_activity(activity_id)
        elif cmd_id == Commands.SELECT_SOURCE:
            source = (params or {}).get("source")
            activity = next((a for a in hub.activities if a.name == source), None)
            if activity is None:
                return StatusCodes.BAD_REQUEST
            await hub.start_activity(activity.identifier)
        elif cmd_id == CMD_SYNC:
            await hub.sync()
        else:
            return StatusCodes.NOT_IMPLEMENTED

        return StatusCodes.OK

    return handle
