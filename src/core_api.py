"""Minimal client for the Remote's Core REST API.

Requests are authenticated with an API key. The web-configurator has no screen
for creating one, so ``create_api_key`` mints it from the web-configurator PIN
the user already has. A key created that way comes back enabled straight away --
the "needs approval on the device" case in the docs does not apply when the
request is itself authenticated.

The PIN is used for that one call and never stored; only the resulting key is.
"""

import json
import logging
import os

import aiohttp

_LOG = logging.getLogger(__name__)

# The driver shares the Remote's network namespace -- there is no bridge or
# firewall -- so the core API is reachable on loopback. Overridable for dev
# runs from a PC, where the driver is not on the Remote at all.
DEFAULT_BASE_URL = "http://127.0.0.1/api"

# The core rejects a larger limit with a 400.
_PAGE_LIMIT = 100

# Basic-auth user for PIN authentication.
_PIN_USER = "web-configurator"

# Scope needed to read entities and create activities. Deliberately not `admin`.
_SCOPE = "configuration"


class CoreApiError(Exception):
    """A Core API request failed, or could not be made at all."""

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)

    @classmethod
    def from_response(cls, status: int, body: str) -> "CoreApiError":
        """Build an error from a failed response, naming the likely cause."""
        if status in (401, 403):
            return cls(
                "the Remote rejected the API key; check it is correct and still "
                "enabled in the web-configurator",
                status,
            )
        return cls(f"the Remote returned {status}: {body[:200]}", status)


class CoreApi:
    """Async client for the subset of the Core API used by the import."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = (
            base_url or os.getenv("UC_CORE_API_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "CoreApi":
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._api_key}"}
        )
        return self

    async def __aexit__(self, *_exc_info) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(self, method: str, path: str, **kwargs):
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}", **kwargs
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    raise CoreApiError.from_response(response.status, body)
                return json.loads(body) if body else None
        except aiohttp.ClientError as err:
            # Surface transport failures as one error type, so callers do not
            # have to know this is built on aiohttp.
            raise CoreApiError(f"cannot reach the Remote at {self._base_url}: {err}") from err

    async def _paged(self, path: str) -> list[dict]:
        """Read every page of a paged collection.

        Note for anyone adding a filter here: the entity filter parameter is
        `intg_ids`, not `integration_id`. The core ignores an unrecognised
        query parameter and returns the unfiltered collection instead, so a
        wrong name fails silently rather than erroring.
        """
        items: list[dict] = []
        page = 1
        while True:
            batch = await self._request(
                "GET", path, params={"page": page, "limit": _PAGE_LIMIT}
            )
            if not batch:
                return items
            items += batch
            if len(batch) < _PAGE_LIMIT:
                return items
            page += 1

    async def entities(self) -> list[dict]:
        """All entities configured on the Remote."""
        return await self._paged("/entities")

    async def activities(self) -> list[dict]:
        """All activities defined on the Remote."""
        return await self._paged("/activities")

    async def create_activity(
        self, name: str, entity_ids: list[str], icon: str | None = None
    ) -> str:
        """Create an activity and return its entity id.

        The create request accepts no sequences -- see ``set_sequences``.
        """
        payload = {"name": {"en": name}, "options": {"entity_ids": list(entity_ids)}}
        if icon:
            payload["icon"] = icon
        created = await self._request("POST", "/activities", json=payload)
        return created["entity_id"]

    async def set_sequences(
        self, entity_id: str, entity_ids: list[str], on: list[dict], off: list[dict]
    ) -> None:
        """Set the on/off sequences of an existing activity.

        Always a second call: `ActivityCreate` carries only name, icon,
        description and `options.entity_ids`.

        `entity_ids` is resent because every entity referenced by a sequence
        must also be included in the activity, and a PATCH replaces the object
        it is given rather than merging into it.
        """
        await self._request(
            "PATCH",
            f"/activities/{entity_id}",
            json={
                "options": {
                    "entity_ids": list(entity_ids),
                    "sequences": {"on": on, "off": off},
                }
            },
        )

    async def delete_activity(self, entity_id: str) -> None:
        """Delete an activity."""
        await self._request("DELETE", f"/activities/{entity_id}")


async def create_api_key(
    pin: str, name: str, base_url: str | None = None
) -> str:
    """Mint an API key from the web-configurator PIN and return it.

    The key is only ever returned by this call, so the caller must persist it.

    Key names are unique: the core answers a repeat with 422 ALREADY_EXISTS.
    Rather than fail, the existing key of the same name is revoked and a fresh
    one issued, which keeps a re-run of setup working.
    """
    url = (base_url or os.getenv("UC_CORE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
    auth = aiohttp.BasicAuth(_PIN_USER, pin)
    payload = {"name": name, "scopes": [_SCOPE]}

    try:
        async with aiohttp.ClientSession(auth=auth) as session:

            async def post() -> tuple[int, str]:
                async with session.post(f"{url}/auth/api_keys", json=payload) as resp:
                    return resp.status, await resp.text()

            status, body = await post()

            if status == 422:
                await _revoke_by_name(session, url, name)
                status, body = await post()

            if status >= 400:
                raise CoreApiError.from_response(status, body)

            api_key = json.loads(body).get("api_key")
            if not api_key:
                raise CoreApiError("the Remote issued no API key")
            return api_key
    except aiohttp.ClientError as err:
        raise CoreApiError(f"cannot reach the Remote at {url}: {err}") from err


async def _revoke_by_name(session, url: str, name: str) -> None:
    """Delete the existing API key with this name, if there is one."""
    async with session.get(f"{url}/auth/api_keys") as resp:
        if resp.status >= 400:
            return
        keys = json.loads(await resp.text())

    for key in keys:
        if key.get("name") == name and key.get("key_id"):
            _LOG.info("Replacing existing API key '%s'", name)
            await session.delete(f"{url}/auth/api_keys/{key['key_id']}")
            return
