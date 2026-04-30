"""Atlassian REST v3 client.

Thin async wrapper. Handles token refresh transparently. Methods return
parsed JSON dicts; rendering and field-shaping are caller's responsibility.

Field metadata is cached separately (see cache.py). The client only knows
about the wire format.
"""

from __future__ import annotations

from typing import Any

import httpx

from jg.auth import AuthError, ensure_token, get_tokens, refresh
from jg.config import Config


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body


class JiraClient:
    def __init__(self, config: Config, cloud_id: str | None = None):
        self.config = config
        self.cloud_id = cloud_id or config.default_cloud_id
        if not self.cloud_id:
            raise AuthError("No cloud_id configured. Run: jg auth login")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> JiraClient:
        token = ensure_token(self.config)
        self._client = httpx.AsyncClient(
            base_url=f"https://api.atlassian.com/ex/jira/{self.cloud_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert self._client is not None
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code == 401:
            tokens = get_tokens()
            if tokens:
                # `refresh()` may raise AuthError (with needs_relogin flag) —
                # propagate so callers can show a clear "re-login" message
                # rather than burying it as a generic 401.
                tokens = refresh(self.config, tokens)
                self._client.headers["Authorization"] = f"Bearer {tokens.access_token}"
                resp = await self._client.request(method, path, **kwargs)
            else:
                raise AuthError("Not logged in. Run: jg auth login")
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # --- Search ---
    async def search_jql(
        self,
        jql: str,
        fields: list[str] | None = None,
        max_results: int = 100,
        next_page_token: str | None = None,
    ) -> dict[str, Any]:
        # Atlassian deprecated /rest/api/3/search in Mar 2025; new endpoint
        # is POST /rest/api/3/search/jql with JSON body and token pagination.
        body: dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields:
            body["fields"] = fields
        if next_page_token:
            body["nextPageToken"] = next_page_token
        return await self._request("POST", "/rest/api/3/search/jql", json=body)

    # --- Issue read ---
    async def get_issue(
        self, key: str, fields: list[str] | None = None, expand: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = expand
        return await self._request("GET", f"/rest/api/3/issue/{key}", params=params)

    # --- Issue write ---
    async def edit_issue(self, key: str, fields: dict[str, Any], update: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"fields": fields}
        if update:
            body["update"] = update
        await self._request("PUT", f"/rest/api/3/issue/{key}", json=body)

    async def create_issue(self, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/rest/api/3/issue", json={"fields": fields})

    # --- Transitions ---
    async def get_transitions(self, key: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/rest/api/3/issue/{key}/transitions")
        return data.get("transitions", [])

    async def transition_issue(
        self, key: str, transition_id: str, resolution: str | None = None
    ) -> None:
        body: dict[str, Any] = {"transition": {"id": transition_id}}
        if resolution:
            body["fields"] = {"resolution": {"name": resolution}}
        await self._request(
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json=body,
        )

    # --- Comments ---
    async def add_comment(self, key: str, body_adf: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/rest/api/3/issue/{key}/comment",
            json={"body": body_adf},
        )

    # --- Links ---
    async def get_link_types(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/rest/api/3/issueLinkType")
        return data.get("issueLinkTypes", [])

    async def create_link(self, link_type: str, inward_key: str, outward_key: str) -> None:
        await self._request(
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            },
        )

    # --- Field metadata ---
    async def get_create_meta(self, project_key: str, issue_type_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{issue_type_id}",
            params={"maxResults": 200},
        )

    # --- User ---
    async def myself(self) -> dict[str, Any]:
        return await self._request("GET", "/rest/api/3/myself")

    async def find_user(self, query: str) -> list[dict[str, Any]]:
        data = await self._request("GET", "/rest/api/3/user/search", params={"query": query})
        return data if isinstance(data, list) else []

    # --- Agile (sprints, backlog) ---
    async def get_sprints(self, board_id: str, state: str = "active,future") -> list[dict[str, Any]]:
        """List sprints on a board. Default state filter excludes closed sprints."""
        data = await self._request(
            "GET",
            f"/rest/agile/1.0/board/{board_id}/sprint",
            params={"state": state, "maxResults": 50},
        )
        return data.get("values", [])

    async def move_to_sprint(self, sprint_id: int, issue_keys: list[str]) -> None:
        await self._request(
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            json={"issues": issue_keys},
        )

    async def move_to_backlog(self, issue_keys: list[str]) -> None:
        await self._request(
            "POST",
            "/rest/agile/1.0/backlog/issue",
            json={"issues": issue_keys},
        )
