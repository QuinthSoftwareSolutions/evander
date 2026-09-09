from __future__ import annotations

import pytest

from evander.admin.services.managed_git_service import AzurePrRequestError
from test_ado_user_connections import _context
from test_pr_publication_policy import publisher


@pytest.mark.parametrize("personal", [True, False])
@pytest.mark.parametrize("partial_failure", [True, False])
def test_publication_links_all_items_and_retries_only_missing_links(
    tmp_path, monkeypatch, personal, partial_failure,
):
    service, remote, args = publisher(tmp_path, monkeypatch, save_pat=personal)
    args["publication_context_id"] = _context(
        service.ado_user_connections,
        source_ref="multi-item-release",
        work_item_ids=["456", "123", "456"],
    )
    args["work_item_id"] = "123"
    args["publication_policy"] = "require_personal" if personal else "allow_bot_fallback"
    remote.created_by = "alice" if personal else "bot"
    request = service._azure_request_json
    reject_link = partial_failure

    def with_link_failure(url, **kwargs):
        if reject_link and "/workitems/456?" in url and kwargs.get("method") == "PATCH":
            raise AzurePrRequestError(403)
        return request(url, **kwargs)

    monkeypatch.setattr(service, "_azure_request_json", with_link_failure)
    result = service.pr_create(**args)
    assert result["success"] is True
    assert result["created"] is True
    assert result["credential_source"] == ("personal_pat" if personal else "bot")
    assert result["work_item_ids"] == ["123", "456"]
    assert result["linked_work_item_ids"] == (["123"] if partial_failure else ["123", "456"])
    assert result["work_item_linked"] is (not partial_failure)
    if partial_failure:
        assert "Work item 456" in result["work_item_link_warning"]
    reject_link = False
    retry = service.pr_create(**args)
    assert retry["created"] is False
    assert retry["work_item_linked"] is True
    assert retry["linked_work_item_ids"] == ["123", "456"]
    assert "work_item_link_warning" not in retry
    assert remote.link_writes == ["123", "456"]
    assert sum(method == "POST" for method, _ in remote.calls) == 1
    for item in remote.work_items.values():
        assert item["relations"] == [{
            "rel": "ArtifactLink",
            "url": "vstfs:///Git/PullRequestId/project-id%2Frepo-id%2F42",
            "attributes": {"name": "Pull Request"},
        }]
