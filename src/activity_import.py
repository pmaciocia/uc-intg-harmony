"""Create one Remote activity per Harmony activity.

Reimplements the Core-API half of the original Docker integration: each Harmony
activity becomes a UC activity whose on-sequence selects that activity as the
source on our media-player entity.

An activity whose name already exists on the Remote is skipped, never modified.
That keeps a re-run idempotent without storing any mapping, and means the import
can never damage an activity the user built by hand.
"""

import logging
from dataclasses import dataclass, field

from const import hub_entity_id
from core_api import CoreApi, CoreApiError
from hub import Hub

_LOG = logging.getLogger(__name__)

# Command ids are entity-type namespaced. These are the values the core itself
# reports in an activity's `included_entities[].entity_commands`.
CMD_SELECT_SOURCE = "media_player.select_source"
CMD_OFF = "media_player.off"

ACTIVITY_ICON = "uc:remote"


class ActivityImportError(Exception):
    """The import could not be started."""


@dataclass
class ImportResult:
    """Outcome of an import run."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-paragraph report for the setup screen."""
        lines = [f"Created {len(self.created)} activity/activities on the Remote."]
        if self.created:
            lines.append("Created: " + ", ".join(self.created))
        if self.skipped:
            lines.append(
                f"Skipped {len(self.skipped)} that already exist and were left "
                "untouched: " + ", ".join(self.skipped)
            )
        if self.failed:
            lines.append("Failed: " + ", ".join(self.failed))
        return "\n".join(lines)


def _entity_name(entity: dict) -> str:
    """Read the English name of an entity, falling back to any translation."""
    names = entity.get("name") or {}
    if isinstance(names, str):
        return names
    return names.get("en") or next(iter(names.values()), "")


def _on_sequence(entity_id: str, activity_name: str) -> list[dict]:
    return [
        {
            "type": "command",
            "command": {
                "entity_id": entity_id,
                "cmd_id": CMD_SELECT_SOURCE,
                "params": {"source": activity_name},
            },
        }
    ]


def _off_sequence(entity_id: str) -> list[dict]:
    # Harmony's PowerOff is hub-global: turning any imported activity off stops
    # whatever is currently running, which is what the original did too.
    return [
        {
            "type": "command",
            "command": {"entity_id": entity_id, "cmd_id": CMD_OFF},
        }
    ]


async def _find_media_player(api: CoreApi, hub: Hub) -> str:
    """Find the Remote's entity id for this hub's media-player.

    Matched by suffix rather than rebuilt from the driver id, because the
    instance segment of the full id (`harmony.main.…`) belongs to the core.
    """
    suffix = hub_entity_id(hub.identifier)
    for entity in await api.entities():
        identifier = entity.get("entity_id", "")
        if entity.get("entity_type") == "media_player" and identifier.endswith(suffix):
            return identifier

    raise ActivityImportError(
        f"the Remote has no media-player entity for hub '{hub.name}'. Add the "
        "hub's entities under Integrations before importing activities."
    )


async def import_activities(hub: Hub, api: CoreApi) -> ImportResult:
    """Create a Remote activity for every Harmony activity not already there."""
    if not hub.connected:
        raise ActivityImportError(f"hub '{hub.name}' is not connected")

    entity_id = await _find_media_player(api, hub)
    existing = {_entity_name(activity).casefold() for activity in await api.activities()}

    result = ImportResult()
    for activity in hub.activities:
        if activity.name.casefold() in existing:
            result.skipped.append(activity.name)
            continue

        try:
            created = await api.create_activity(
                activity.name, [entity_id], ACTIVITY_ICON
            )
            await api.set_sequences(
                created,
                [entity_id],
                _on_sequence(entity_id, activity.name),
                _off_sequence(entity_id),
            )
        except CoreApiError:
            # One bad activity must not cost the user the rest of the import.
            _LOG.exception("Cannot import activity '%s'", activity.name)
            result.failed.append(activity.name)
        else:
            result.created.append(activity.name)

    _LOG.info(
        "Activity import for '%s': %d created, %d skipped, %d failed",
        hub.name,
        len(result.created),
        len(result.skipped),
        len(result.failed),
    )
    return result
