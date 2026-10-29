


def test_ado_comment_pagination_preserves_edits_and_reports_limits():
    import httpx
    from evander_azure_devops_addon.issues import AzureDevOpsIssueAdapter

    adapter = AzureDevOpsIssueAdapter.__new__(AzureDevOpsIssueAdapter)
    adapter.comments_max_count = 1
    requests = []

    def handle(request):
        requests.append(request)
        token = request.url.params.get("continuationToken")
        body = {
            "comments": [
                {
                    "id": 2 if not token else 1,
                    "text": "Clarification",
                    "version": 3,
                    "modifiedDate": "2026-09-15T00:00:00Z",
                }
            ]
        }
        if not token:
            body["continuationToken"] = "page-2"
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        paging = {}
        values = dict(
            organization_url="https://dev.azure.com/org",
            project="Project",
            auth_header="test",
            remote_issue_id="42",
            paging=paging,
        )
        result = adapter._fetch_comments(client, **values, page_limit=10)
        assert [r["id"] for r in result] == ["2", "1"]
        assert result[0]["version"] == 3 and result[0]["modified_at"]
        assert paging["complete"] and paging["pages"] == 2
        adapter._fetch_comments(client, **values, page_limit=1)
        assert not paging["complete"] and paging["has_more"]
