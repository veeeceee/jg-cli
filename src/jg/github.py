"""GitHub integration via wrapping the `gh` CLI.

For PR lists we use GraphQL through `gh api graphql` so we can get
`reviewDecision` in a single round-trip. `gh search prs` doesn't expose that
field; `gh pr list` does, but only one repo at a time.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


class GhError(Exception):
    pass


def gh_available() -> bool:
    return subprocess.run(["which", "gh"], capture_output=True).returncode == 0


_USER_CACHE: str | None = None


def current_user() -> str | None:
    """Authenticated gh user's login. Cached for the process."""
    global _USER_CACHE
    if _USER_CACHE:
        return _USER_CACHE
    try:
        res = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    login = res.stdout.strip()
    _USER_CACHE = login or None
    return _USER_CACHE


def gh_json(args: list[str]) -> Any:
    """Run `gh ...` with --json flag handling. Caller passes the full arg list."""
    res = subprocess.run(["gh", *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise GhError(res.stderr.strip() or f"gh exited {res.returncode}")
    if not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return res.stdout


_PR_FIELDS_GQL = """
number
title
isDraft
reviewDecision
url
updatedAt
state
author { login }
repository { nameWithOwner }
"""

_GQL_SEARCH_QUERY = f"""
query($q: String!, $n: Int!) {{
  search(type: ISSUE, query: $q, first: $n) {{
    nodes {{ ... on PullRequest {{ {_PR_FIELDS_GQL} }} }}
  }}
}}
"""


def _gql_search(query: str, limit: int) -> list[dict[str, Any]]:
    res = subprocess.run(
        [
            "gh", "api", "graphql",
            "-f", f"query={_GQL_SEARCH_QUERY}",
            "-F", f"q={query}",
            "-F", f"n={limit}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise GhError(res.stderr.strip() or f"gh api exited {res.returncode}")
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise GhError(f"unexpected gh output: {e}") from e
    if "errors" in data:
        raise GhError(str(data["errors"]))
    nodes = (data.get("data") or {}).get("search", {}).get("nodes") or []
    # Flatten repository.nameWithOwner shape to match the search-prs fields.
    out: list[dict[str, Any]] = []
    for n in nodes:
        if not n:
            continue
        repo = (n.get("repository") or {}).get("nameWithOwner") or "?"
        out.append({
            "number": n.get("number"),
            "title": n.get("title", ""),
            "isDraft": n.get("isDraft", False),
            "reviewDecision": n.get("reviewDecision"),
            "url": n.get("url"),
            "updatedAt": n.get("updatedAt"),
            "state": n.get("state"),
            "author": n.get("author") or {},
            "repository": {"nameWithOwner": repo},
        })
    return out


def my_open_prs(limit: int = 50) -> list[dict[str, Any]]:
    user = current_user()
    if not user:
        raise GhError("could not determine gh user — run `gh auth login`")
    return _gql_search(f"author:{user} is:open is:pr archived:false", limit)


def review_requested_prs(limit: int = 50) -> list[dict[str, Any]]:
    user = current_user()
    if not user:
        raise GhError("could not determine gh user — run `gh auth login`")
    return _gql_search(f"review-requested:{user} is:open is:pr archived:false", limit)


def prs_mentioning(key: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find PRs whose title or body mentions a Jira issue key."""
    return _gql_search(f"{key} in:title,body is:pr", limit)


_REPO_GQL = """
query($n: Int!) {
  viewer {
    repositories(
      first: $n,
      orderBy: {field: PUSHED_AT, direction: DESC},
      ownerAffiliations: [OWNER, ORGANIZATION_MEMBER, COLLABORATOR],
      isArchived: false
    ) {
      nodes {
        nameWithOwner
        description
        sshUrl
        url
        updatedAt
        pushedAt
        isPrivate
        isFork
        isArchived
        owner { login }
      }
    }
  }
}
"""


def my_repos(limit: int = 200) -> list[dict[str, Any]]:
    """All repos the user has access to (own + org member + collaborator),
    sorted by most recently pushed. Archives excluded.

    Uses GraphQL with `ownerAffiliations` so org repos appear alongside
    personal ones — `gh repo list` defaults to OWNER-only."""
    res = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={_REPO_GQL}", "-F", f"n={limit}"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise GhError(res.stderr.strip() or f"gh api exited {res.returncode}")
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise GhError(f"unexpected gh output: {e}") from e
    if "errors" in data:
        raise GhError(str(data["errors"]))
    nodes = (data.get("data") or {}).get("viewer", {}).get("repositories", {}).get("nodes") or []
    # Use pushedAt as the "updated" signal since it's more meaningful than updatedAt
    # (which changes on issue/PR activity, not just code).
    for n in nodes:
        n.setdefault("updatedAt", n.get("pushedAt") or n.get("updatedAt"))
    return nodes


def pr_detail(url_or_repo_num: str) -> dict[str, Any]:
    """Full PR detail. Accepts a URL or '<owner>/<repo>#<num>' style identifier."""
    return gh_json([
        "pr", "view", url_or_repo_num,
        "--json", "number,title,body,state,isDraft,reviewDecision,author,"
                  "assignees,baseRefName,headRefName,changedFiles,additions,"
                  "deletions,labels,url,mergeable,createdAt,updatedAt,"
                  "statusCheckRollup,reviewRequests,comments",
    ]) or {}


def repo_detail(name_with_owner: str) -> dict[str, Any]:
    return gh_json([
        "repo", "view", name_with_owner,
        "--json", "nameWithOwner,description,url,sshUrl,defaultBranchRef,"
                  "stargazerCount,languages,primaryLanguage,pushedAt,createdAt,"
                  "homepageUrl,isPrivate,isFork,isArchived,licenseInfo,"
                  "diskUsage,openGraphImageUrl,repositoryTopics,forkCount",
    ]) or {}


_REPO_PULSE_GQL = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    openPRs: pullRequests(states: OPEN) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
    recentPRs: pullRequests(states: OPEN, first: 5, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number
        title
        isDraft
        reviewDecision
        updatedAt
        author { login }
      }
    }
  }
}
"""


def repo_pulse(name_with_owner: str) -> dict[str, Any]:
    """Open-PR/issue counts + 5 most-recent open PRs. Single GraphQL call."""
    if "/" not in name_with_owner:
        return {}
    owner, name = name_with_owner.split("/", 1)
    res = subprocess.run(
        [
            "gh", "api", "graphql",
            "-f", f"query={_REPO_PULSE_GQL}",
            "-F", f"owner={owner}",
            "-F", f"name={name}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return {}
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}
    return ((data or {}).get("data") or {}).get("repository") or {}
