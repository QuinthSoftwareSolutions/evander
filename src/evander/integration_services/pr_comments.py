"""GitHub comment ownership checks and review-thread resolution."""

from __future__ import annotations

import re
from typing import Any

import httpx

from evander.ado_builds import positive_id


class GitHubPrComments:
    def __init__(self, client: httpx.Client, *, api_base_url: str, headers: dict[str, str]) -> None:
        self.client = client
        self.api_base_url = api_base_url.rstrip("/")
        self.headers = headers
        self.graphql_requests = 0

    @staticmethod
    def _repo(repo: str) -> str:
        parts = repo.strip().split("/")
        if len(parts) != 2 or any(
            part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts
        ):
            raise ValueError("GitHub repo must be an owner/repository name")
        return "/".join(parts)

    def _comment(
        self, *, repo: str, pr_id: str, comment_id: str, feedback_kind: str
    ) -> tuple[str, dict[str, Any]]:
        if feedback_kind not in {"issue_comment", "review_comment"}:
            raise ValueError(
                "GitHub comment deletion requires feedback_kind=issue_comment or review_comment"
            )
        category = "issues" if feedback_kind == "issue_comment" else "pulls"
        url = f"{self.api_base_url}/repos/{repo}/{category}/comments/{comment_id}"
        response = self.client.get(url, headers=self.headers)
        response.raise_for_status()
        comment = response.json()
        owner_url = comment.get("issue_url" if category == "issues" else "pull_request_url")
        expected_url = f"{self.api_base_url}/repos/{repo}/{category}/{pr_id}"
        if not isinstance(owner_url, str) or owner_url.casefold() != expected_url.casefold():
            raise ValueError("GitHub comment does not belong to the requested pull request")
        # Issue comments can also belong to ordinary issues; verify the target is a PR.
        if category == "issues":
            response = self.client.get(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}", headers=self.headers
            )
            response.raise_for_status()
        return url, comment

    def delete_comment(
        self, *, repo: str, pr_id: str, comment_id: str, feedback_kind: str | None
    ) -> dict[str, Any]:
        repo = self._repo(repo)
        pr_id = str(positive_id(pr_id.strip(), "pr_id"))
        comment_id = str(positive_id(comment_id.strip(), "comment_id"))
        url, _ = self._comment(
            repo=repo, pr_id=pr_id, comment_id=comment_id, feedback_kind=feedback_kind or ""
        )
        response = self.client.delete(url, headers=self.headers)
        response.raise_for_status()
        return {
            "status": "deleted",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "comment_id": comment_id,
            "feedback_kind": feedback_kind,
            "deleted": True,
        }

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self.graphql_requests >= 100:
            raise ValueError("GitHub review-thread lookup exceeded its request limit")
        self.graphql_requests += 1
        base = self.api_base_url
        url = base[:-3] + "/graphql" if base.endswith("/v3") else base + "/graphql"
        response = self.client.post(
            url, headers=self.headers, json={"query": query, "variables": variables}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ValueError("GitHub review-thread GraphQL request failed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("GitHub returned invalid review-thread data")
        return data

    @staticmethod
    def _next_cursor(connection: dict[str, Any], seen: set[str]) -> str | None:
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return None
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError("GitHub returned a non-advancing review-thread cursor")
        seen.add(cursor)
        return cursor

    def _thread_for_comment(self, *, repo: str, pr_id: str, comment_id: str) -> dict[str, Any]:
        _, comment = self._comment(
            repo=repo, pr_id=pr_id, comment_id=comment_id, feedback_kind="review_comment"
        )
        comment_node_id = comment.get("node_id")
        if not comment_node_id:
            raise ValueError("GitHub returned a review comment without a node ID")
        owner, name = repo.split("/")
        query = """
            query($owner: String!, $name: String!, $number: Int!, $after: String) {
              repository(owner: $owner, name: $name) {
                pullRequest(number: $number) {
                  reviewThreads(first: 100, after: $after) {
                    nodes { id isResolved comments(first: 100) {
                      nodes { id } pageInfo { hasNextPage endCursor }
                    } }
                    pageInfo { hasNextPage endCursor }
                  }
                }
              }
            }
        """
        after = None
        seen: set[str] = set()
        while True:
            data = self._graphql(
                query, {"owner": owner, "name": name, "number": int(pr_id), "after": after}
            )
            repository = data.get("repository") or {}
            pull_request = repository.get("pullRequest") or {}
            threads = pull_request.get("reviewThreads")
            if not isinstance(threads, dict):
                raise ValueError("GitHub pull request review threads are unavailable")
            for thread in threads["nodes"]:
                comments = thread["comments"]
                comment_cursors: set[str] = set()
                while True:
                    if any(item["id"] == comment_node_id for item in comments["nodes"]):
                        return thread
                    cursor = self._next_cursor(comments, comment_cursors)
                    if cursor is None:
                        break
                    data = self._graphql(
                        """
                        query($id: ID!, $after: String!) {
                          node(id: $id) { ... on PullRequestReviewThread {
                            comments(first: 100, after: $after) {
                              nodes { id } pageInfo { hasNextPage endCursor }
                            }
                          } }
                        }
                        """,
                        {"id": thread["id"], "after": cursor},
                    )
                    comments = (data.get("node") or {}).get("comments")
                    if not isinstance(comments, dict):
                        raise ValueError("GitHub review-thread comments are unavailable")
            after = self._next_cursor(threads, seen)
            if after is None:
                raise ValueError("GitHub comment was not found in a resolvable review thread")

    def resolve_comment(
        self, *, repo: str, pr_id: str, thread_id: str | None, comment_id: str | None
    ) -> dict[str, Any]:
        repo = self._repo(repo)
        pr_id = str(positive_id(pr_id.strip(), "pr_id"))
        if bool(thread_id) == bool(comment_id):
            raise ValueError("Supply exactly one GitHub review thread_id or review comment_id")
        if comment_id:
            comment_id = str(positive_id(comment_id.strip(), "comment_id"))
            thread = self._thread_for_comment(repo=repo, pr_id=pr_id, comment_id=comment_id)
        else:
            data = self._graphql(
                """
                query($id: ID!) {
                  node(id: $id) { ... on PullRequestReviewThread {
                    id isResolved pullRequest { number repository { nameWithOwner } }
                  } }
                }
                """,
                {"id": thread_id},
            )
            thread = data.get("node") or {}
            pull_request = thread.get("pullRequest") or {}
            repository = pull_request.get("repository") or {}
            if (
                str(pull_request.get("number")) != pr_id
                or str(repository.get("nameWithOwner", "")).casefold() != repo.casefold()
            ):
                raise ValueError(
                    "GitHub review thread does not belong to the requested pull request"
                )
        thread_id = thread.get("id")
        if not thread_id:
            raise ValueError("GitHub returned a review thread without an ID")
        already_resolved = thread.get("isResolved") is True
        if not already_resolved:
            data = self._graphql(
                """
                mutation($id: ID!) {
                  resolveReviewThread(input: {threadId: $id}) { thread { id isResolved } }
                }
                """,
                {"id": thread_id},
            )
            resolved_thread = (data.get("resolveReviewThread") or {}).get("thread") or {}
            if (
                resolved_thread.get("id") != thread_id
                or resolved_thread.get("isResolved") is not True
            ):
                raise ValueError("GitHub did not confirm review-thread resolution")
        return {
            "status": "resolved",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "thread_id": thread_id,
            "comment_id": comment_id,
            "resolved": True,
            "already_resolved": already_resolved,
        }
