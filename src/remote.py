"""Remote entity representing a device behind a Harmony hub."""

import logging
from typing import Any

from ucapi import Remote, StatusCodes
from ucapi.remote import Attributes, Commands, Features, States
from ucapi.ui import Size, UiPage, create_ui_text

from const import UI_GRID_HEIGHT, UI_GRID_WIDTH, device_entity_id
from hub import Device, Hub

_LOG = logging.getLogger(__name__)

_FEATURES = [Features.ON_OFF, Features.SEND_CMD]
_PAGE_SIZE = UI_GRID_WIDTH * UI_GRID_HEIGHT

_POWER_ON = ("PowerOn", "PowerToggle")
_POWER_OFF = ("PowerOff", "PowerToggle")


def create(hub: Hub, device: Device) -> Remote:
    """Build the remote entity for a device."""
    commands = [command.name for command in device.commands]
    return Remote(
        device_entity_id(hub.identifier, device.identifier),
        {"en": device.name},
        _FEATURES,
        attributes(hub),
        simple_commands=commands,
        ui_pages=_ui_pages(device),
        cmd_handler=_handler(hub, device),
    )


def attributes(hub: Hub) -> dict[str, Any]:
    """Build the current attribute state of a device.

    The Harmony hub reports no per-device power feedback, so a device is
    reported as ON whenever its hub is reachable.
    """
    return {Attributes.STATE: States.ON if hub.connected else States.UNAVAILABLE}


def _ui_pages(device: Device) -> list[UiPage]:
    pages = []
    for index in range(0, len(device.commands), _PAGE_SIZE):
        chunk = device.commands[index : index + _PAGE_SIZE]
        page = UiPage(
            f"page{index // _PAGE_SIZE + 1}",
            f"{device.name} {index // _PAGE_SIZE + 1}",
            grid=Size(UI_GRID_WIDTH, UI_GRID_HEIGHT),
        )
        for position, command in enumerate(chunk):
            page.add(
                create_ui_text(
                    command.label,
                    position % UI_GRID_WIDTH,
                    position // UI_GRID_WIDTH,
                    cmd=command.name,
                )
            )
        pages.append(page)
    return pages


def _power_command(device: Device, candidates: tuple[str, ...]) -> str | None:
    names = {command.name for command in device.commands}
    return next((name for name in candidates if name in names), None)


def _handler(hub: Hub, device: Device):
    known = {command.name for command in device.commands}

    # The parameter must be named `websocket`: ucapi inspects the handler
    # signature for that name to decide how to invoke it.
    async def handle(
        entity: Remote, cmd_id: str, params: dict[str, Any] | None, websocket=None
    ) -> StatusCodes:
        _LOG.debug("%s: %s %s", entity.id, cmd_id, params)
        params = params or {}

        if cmd_id == Commands.SEND_CMD:
            command = params.get("command")
            repeat = int(params.get("repeat", 1))
        elif cmd_id in (Commands.ON, Commands.OFF):
            command = _power_command(
                device, _POWER_ON if cmd_id == Commands.ON else _POWER_OFF
            )
            repeat = 1
        elif cmd_id in known:
            command = cmd_id
            repeat = 1
        else:
            return StatusCodes.NOT_IMPLEMENTED

        if command not in known:
            return StatusCodes.BAD_REQUEST

        for _ in range(repeat):
            await hub.send_command(device.identifier, command)

        return StatusCodes.OK

    return handle
