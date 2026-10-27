"""Customer-neutral, inactive-by-default example composed entirely from existing nodes."""

from __future__ import annotations


MENTION = r"(?:@<[^>]+>|<a\b[^>]*data-vss-mention[^>]*>.*?</a>|@[^!\r\n]+)?"
COMMAND_ROUTE = rf"(?is)^\s*{MENTION}\s*!EXPLAIN\b[\s\S]*"
COMMAND_PARSE = (
    rf"(?is:^\s*{MENTION}\s*!EXPLAIN\b)\s+(?P<target>[a-z](?:[a-z0-9-]{{0,18}}[a-z0-9])?)\s*"
)


def ref(path: str) -> str:
    return "${{" + path + "}}"


def build_workflow() -> dict:
    nodes, edges = [], []
    feedback = "explain-feedback.feedback."
    directory = "root/example"
    session = ref("explain-sessions.sessions.0.session_id")
    request_key = ":".join(
        ref(feedback + key) for key in ("repo", "pr_id", "thread_id", "comment_id")
    )
    ready_key = request_key + ":" + ref("explain-builds.builds.0.run_id")
    ready_path = (
        "demo-video/preflight/"
        + ref(feedback + "thread_id")
        + "-"
        + ref(feedback + "comment_id")
        + ".txt"
    )

    def node(name, kind, config):
        nodes.append(
            {
                "id": name,
                "display_name": name.replace("explain-", "").replace("-", " "),
                "type": kind,
                "config": config,
            }
        )
        return name

    def edge(source, target, port=None):
        edges.append(
            {"source_id": source, "target_id": target, **({"source_port": port} if port else {})}
        )

    def reply(name, message):
        return node(
            name,
            "effect:pull-request:reply",
            {
                "provider": "azure_devops",
                "directory_path": directory,
                "project": "Example Project",
                "repo": ref(feedback + "repo"),
                "pr_id": ref(feedback + "pr_id"),
                "thread_id": ref(feedback + "thread_id"),
                "comment_id": ref(feedback + "comment_id"),
                "body_text": message,
                "idempotency_key": "pr-explain:" + request_key + ":" + name,
            },
        )

    def check(name, when, source, message):
        node(name, "gate:validate", {"checks": [{"code": name, "message": message, "when": when}]})
        edge(source, name)
        error = reply(name + "-rejected", message)
        edge(name, error, "invalid")
        return name

    node(
        "explain-feedback",
        "trigger:pull-request:new-feedback",
        {
            "provider": "ado",
            "directory_path": directory,
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "repo": "sample-app",
            "status": "active",
            "tags": "review-agent or review-in-progress",
            "agent_author_names": ["Review Bot"],
            "agent_mention_names": ["Review Bot"],
            "suppress_agent_authored_feedback": True,
            "top": 50,
        },
    )
    node(
        "explain-human",
        "gate:filter",
        {
            "when": {
                "all": [
                    {
                        "left": ref(feedback + "metadata.agent_directed_author_self ?? false"),
                        "not_equals": True,
                    },
                    {"left": ref(feedback + "author"), "not_equals": "Review Bot"},
                ]
            }
        },
    )
    edge("explain-feedback", "explain-human")
    node(
        "explain-route",
        "gate:switch",
        {
            "cases": [
                {
                    "label": "Explain",
                    "port": "explain",
                    "when": {"left": ref(feedback + "body"), "matches_regex": COMMAND_ROUTE},
                }
            ],
            "default_port": "standard",
        },
    )
    edge("explain-human", "explain-route")
    node(
        "explain-standard",
        "effect:event:emit",
        {
            "event_type": "example.pr_feedback.standard",
            "payload": {"feedback_id": ref(feedback + "feedback_id")},
        },
    )
    edge("explain-route", "explain-standard", "standard")
    node(
        "explain-dedupe",
        "gate:dedupe",
        {
            "key": "pr-explain:"
            + ref(feedback + "repo")
            + ":"
            + ref(feedback + "pr_id")
            + ":"
            + ref(feedback + "feedback_id"),
            "ttl": "P365D",
        },
    )
    edge("explain-route", "explain-dedupe", "explain")
    node(
        "explain-parse",
        "query:text:regex",
        {"text": ref(feedback + "body"), "pattern": COMMAND_PARSE, "require_match": True},
    )
    edge("explain-dedupe", "explain-parse")
    node(
        "explain-dns",
        "query:text:regex",
        {
            "text": ref("explain-parse.groups.target"),
            "operation": "replace",
            "pattern": "-",
            "replacement": "",
        },
    )
    edge("explain-parse", "explain-dns")
    check(
        "explain-valid-name",
        {"left": ref("explain-dns.value"), "matches_regex": r"\A[a-z0-9]{3,20}\Z"},
        "explain-dns",
        "Use `!EXPLAIN <target>` with an existing lowercase demo name. No deployment was queued.",
    )
    public = "https://" + ref("explain-dns.value") + ".demo.example.test"
    node(
        "explain-login",
        "query:website-session:resolve-profile",
        {"browser_origin": public + ":8443", "login_origin": public + ":5001"},
    )
    edge("explain-valid-name", "explain-login", "valid")
    check(
        "explain-login-ready",
        {"left": ref("explain-login.ready"), "equals": True},
        "explain-login",
        "The target has no ready managed website login. Ask the operator to configure and test it; no deployment was queued.",
    )
    node(
        "explain-sessions",
        "query:session:list",
        {
            "directory_path": directory,
            "active_only": False,
            "limit": 2,
            "metadata_equals": {
                "workflow": "pr-review",
                "pr_monitoring": True,
                "pr_repo": ref(feedback + "repo"),
                "pr_id": ref(feedback + "pr_id"),
            },
        },
    )
    edge("explain-login-ready", "explain-sessions", "valid")
    check(
        "explain-one-session",
        {"left": ref("explain-sessions.count"), "equals": 1},
        "explain-sessions",
        "Exactly one existing PR review session is required. Ask the operator to resolve missing or duplicate sessions; no deployment was queued.",
    )
    pr_config = {
        "provider": "azure_devops",
        "directory_path": directory,
        "project": "Example Project",
        "repo": ref(feedback + "repo"),
        "pr_id": ref(feedback + "pr_id"),
        "include_commits": False,
        "include_reviewers": False,
    }
    node("explain-pr", "query:pull-request:get", pr_config)
    edge("explain-one-session", "explain-pr", "valid")
    check(
        "explain-active",
        {
            "all": [
                {"left": ref("explain-pr.status"), "equals": "active"},
                {"left": ref("explain-pr.source_commit"), "is_not_null": True},
            ]
        },
        "explain-pr",
        "The PR is closed or has no readable source revision. No deployment was queued.",
    )
    node(
        "explain-branch",
        "query:text:regex",
        {
            "text": ref("explain-pr.source_branch"),
            "operation": "replace",
            "pattern": "^refs/heads/",
            "replacement": "",
        },
    )
    edge("explain-active", "explain-branch", "valid")
    node(
        "explain-builds",
        "query:ado:builds",
        {
            "directory_path": directory,
            "project": "Example Project",
            "pipeline_id": 101,
            "repository_id": ref("explain-pr.raw.repository.id"),
            "limit": 1,
            "filters": [
                {
                    "branch": ref("explain-pr.source_branch"),
                    "source_version": ref("explain-pr.source_commit"),
                },
                {
                    "branch": "refs/pull/" + ref(feedback + "pr_id") + "/merge",
                    "source_version": ref("explain-pr.raw.lastMergeCommit.commitId ?? null"),
                },
            ],
        },
    )
    edge("explain-branch", "explain-builds")
    check(
        "explain-found",
        {"left": ref("explain-builds.count"), "greater_than": 0},
        "explain-builds",
        "No build exists for the current PR revision. Queue a build, then post a new `!EXPLAIN <target>` comment.",
    )
    node(
        "explain-build-state",
        "gate:switch",
        {
            "cases": [
                {
                    "label": "Successful",
                    "port": "ready",
                    "when": {"left": ref("explain-builds.builds.0.success"), "equals": True},
                },
                {
                    "label": "Wait",
                    "port": "wait",
                    "when": {
                        "left": ref("explain-builds.builds.0.status"),
                        "in": ["notstarted", "inprogress", "postponed", "cancelling"],
                    },
                },
            ],
            "default_port": "failed",
        },
    )
    edge("explain-found", "explain-build-state", "valid")
    for port in ("ready", "wait"):
        reply(
            "explain-ack-" + port,
            "Accepted `!EXPLAIN "
            + ref("explain-parse.groups.target")
            + "`. I will check the matching build and media tools, redeploy the demo, then produce a narrated explanation. Please coordinate use of the target while this runs.",
        )
        edge("explain-build-state", "explain-ack-" + port, port)
    reply(
        "explain-build-failed",
        "The newest matching build did not succeed. Queue a new build and post a new `!EXPLAIN <target>` comment; no deployment was queued.",
    )
    edge("explain-build-state", "explain-build-failed", "failed")
    node("explain-acknowledged", "gate:merge", {})
    for port in ("ready", "wait"):
        edge("explain-ack-" + port, "explain-acknowledged")
    node(
        "explain-wait",
        "effect:ado:build-wait",
        {
            "directory_path": directory,
            "project": "Example Project",
            "run_id": ref("explain-builds.builds.0.run_id"),
            "poll_interval_seconds": 10,
            "timeout_seconds": 3600,
        },
    )
    edge("explain-acknowledged", "explain-wait")
    node(
        "explain-renew",
        "effect:session:renew-if-expired",
        {
            "directory_path": directory,
            "target": session,
            "extend_by": "8h",
            "renew_if_expires_within": "8h",
        },
    )
    edge("explain-wait", "explain-renew")
    node(
        "explain-copy-tools",
        "effect:agent:copy-files",
        {
            "target": session,
            "vm_user": "agent",
            "files": [
                {
                    "source_path": "/srv/evander/demo-video/toolchain.tar.gz",
                    "destination_path": "/home/agent/.evander/demo-video-toolchain.tar.gz",
                    "overwrite": True,
                },
                {
                    "source_path": "/srv/evander/demo-video/worker.py",
                    "destination_path": "/home/agent/.evander/demo-video-bootstrap.py",
                    "overwrite": True,
                },
            ],
        },
    )
    edge("explain-renew", "explain-copy-tools")
    node(
        "explain-install-tools",
        "effect:agent:exec",
        {
            "target": session,
            "vm_user": "agent",
            "command": "python3 /home/agent/.evander/demo-video-bootstrap.py install --archive /home/agent/.evander/demo-video-toolchain.tar.gz --root /home/agent/.evander/demo-video",
            "timeout_seconds": 120,
            "check": True,
        },
    )
    edge("explain-copy-tools", "explain-install-tools")
    node(
        "explain-skills",
        "effect:agent:install-skills",
        {"target": session, "vm_user": "agent", "codex_skills": ["demo-video"]},
    )
    edge("explain-install-tools", "explain-skills")
    node("explain-session", "query:session-context", {"target": session})
    edge("explain-skills", "explain-session")
    check(
        "explain-conversation",
        {
            "all": [
                {
                    "left": ref("explain-session.metadata.agent_thread_id ?? null"),
                    "is_not_null": True,
                },
                {"left": ref('explain-session.metadata.agent_thread_id ?? ""'), "not_equals": ""},
            ]
        },
        "explain-session",
        "The existing review conversation is unavailable. Ask the operator to restore it; no deployment was queued.",
    )
    node(
        "explain-preflight",
        "effect:agent:enqueue-prompt",
        {
            "target": session,
            "vm_user": "agent",
            "resume_mode": "same_context",
            "resume_thread_id": ref("explain-session.metadata.agent_thread_id"),
            "queue_key": "pr-explain:" + request_key + ":preflight",
            "prompt": "Perform only the demo-video readiness procedure. Do not access or change the target application. Use the demo-video skill: call the granted demo-narration.synthesize connector with text 'Narration is ready.', copy its WAV artifact to this workspace, run capture.mjs preflightCapture on an empty local page, then worker.py preflight with that --audio and --recording. Only after all checks pass write the readiness receipt with --receipt /artifacts/"
            + ready_path
            + " --request-key "
            + ready_key
            + ". The toolchain is /home/agent/.evander/demo-video/current. Preserve existing approval rules. If a grant, approval, narration config or tool is unavailable, report the issue and leave no readiness receipt. Deployment has not started.",
        },
    )
    edge("explain-conversation", "explain-preflight", "valid")
    node(
        "explain-read-ready",
        "query:agent:artifact-text",
        {"session_id": session, "path": ready_path, "vm_user": "agent"},
    )
    edge("explain-preflight", "explain-read-ready", "completed")
    check(
        "explain-media-ready",
        {"left": ref("explain-read-ready.content"), "equals": "ready:" + ready_key + "\n"},
        "explain-read-ready",
        "Media or narration readiness did not pass. No deployment was queued; ask the operator to inspect this request.",
    )
    node("explain-current-pr", "query:pull-request:get", pr_config.copy())
    edge("explain-media-ready", "explain-current-pr", "valid")
    check(
        "explain-unchanged",
        {
            "all": [
                {"left": ref("explain-current-pr.status"), "equals": "active"},
                {
                    "left": ref("explain-current-pr.source_commit"),
                    "equals": ref("explain-pr.source_commit"),
                },
                {
                    "left": ref('explain-current-pr.raw.lastMergeCommit.commitId ?? ""'),
                    "equals": ref('explain-pr.raw.lastMergeCommit.commitId ?? ""'),
                },
            ]
        },
        "explain-current-pr",
        "The PR changed or closed during preparation. No deployment was queued; post a new command for its new revision.",
    )
    node(
        "explain-deploy",
        "effect:ado:pipeline-run",
        {
            "directory_path": directory,
            "project": "Example Project",
            "pipeline_id": 102,
            "branch": "main",
            "poll_interval_seconds": 10,
            "timeout_seconds": 3600,
            "check": True,
            "reject_running_parameters": {"resourceBaseName": ref("explain-parse.groups.target")},
            "template_parameters": {
                "operation": "redeploy",
                "resourceBaseName": ref("explain-parse.groups.target"),
                "databaseAction": "preserve",
                "seedKind": "none",
                "configurationKind": "minimal",
                "buildBranchName": ref("explain-branch.value"),
                "buildRunId": ref("explain-builds.builds.0.run_id"),
                "buildNumber": ref("explain-builds.builds.0.build_number"),
                "buildPullRequestId": ref(feedback + "pr_id"),
            },
        },
    )
    edge("explain-unchanged", "explain-deploy", "valid")
    targets = {}
    for alias, suffix in (("demo-ui", ""), ("demo-admin", ":8443"), ("demo-api", ":5001")):
        targets[alias] = {"base_url": public + suffix}
        if alias == "demo-admin":
            targets[alias]["managed_website_session"] = ref("explain-login.profile_id")
    node(
        "explain-targets",
        "effect:session:update-metadata",
        {"target": session, "metadata": {"http_targets": {"targets": targets}}},
    )
    edge("explain-deploy", "explain-targets")
    node(
        "explain-prompt",
        "effect:agent:enqueue-prompt",
        {
            "target": session,
            "vm_user": "agent",
            "resume_mode": "same_context",
            "resume_thread_id": ref("explain-session.metadata.agent_thread_id"),
            "queue_key": "pr-explain:" + request_key + ":video",
            "prompt": "Use the installed demo-video skill as the authoritative guidance for preparation, presentation, narration, verification and publication. Produce an English explanation of this pinned PR using the supplied request context. The host has already deployed the build. Do not redeploy, reset the database, modify application source or push commits. Preserve unrelated data and local work; coordinate target use manually.\n\nRepository: "
            + ref(feedback + "repo")
            + "\nPR: "
            + ref(feedback + "pr_id")
            + "\nFeedback: "
            + ref(feedback + "feedback_id")
            + "\nThread: "
            + ref(feedback + "thread_id")
            + "\nComment: "
            + ref(feedback + "comment_id")
            + "\nTarget: "
            + ref("explain-parse.groups.target")
            + "\nSource commit: "
            + ref("explain-pr.source_commit")
            + "\nBuild revision: "
            + ref("explain-builds.builds.0.source_version")
            + "\nBuild run: "
            + ref("explain-builds.builds.0.run_id")
            + "\nBuild number: "
            + ref("explain-builds.builds.0.build_number")
            + "\nBuild URL: "
            + ref("explain-builds.builds.0.run_url")
            + "\nDeployment: "
            + ref("explain-deploy.run_url")
            + "\nPublic UI: "
            + public
            + "\nBrowser target: demo-admin (managed website login). Use the seeded helper; never print or persist authentication state. Keep the original PR title in the manifest.\n\nUse post_pr_feedback_reply(provider=azure_devops) for the supplied thread/comment. Follow the skill's publication receipts and idempotency key, preserve file approvals, and report any demonstration or review gaps honestly.",
        },
    )
    edge("explain-targets", "explain-prompt")
    node(
        "explain-finished",
        "effect:session:update-metadata",
        {
            "target": session,
            "metadata": {
                "demo_video": {
                    "agent_completed": True,
                    "feedback_id": ref(feedback + "feedback_id"),
                    "build_run_id": ref("explain-builds.builds.0.run_id"),
                }
            },
        },
    )
    edge("explain-prompt", "explain-finished", "completed")
    # Queued native work retains the existing prompt queue's retry semantics.
    for phase in ("preflight", "prompt"):
        switch = "explain-" + phase + "-failure"
        node(
            switch,
            "gate:switch",
            {
                "cases": [
                    {
                        "label": "Queued",
                        "port": "queued",
                        "when": {
                            "left": ref("explain-" + phase + ".queued ?? false"),
                            "equals": True,
                        },
                    }
                ],
                "default_port": "failed",
            },
        )
        edge("explain-" + phase, switch, "error")
        reply(
            switch + "-queued",
            "The explanation request remains queued for automatic retry. Please do not post a second command while it is pending; ask the operator to inspect the queue if it does not resume.",
        )
        reply(
            switch + "-failed",
            "The explanation agent failed or timed out. Completed media may be recoverable; the operator should resume that stage. Deployment or prepared data may remain installed. Check this request before retrying.",
        )
        edge(switch, switch + "-queued", "queued")
        edge(switch, switch + "-failed", "failed")
    for item in list(nodes):
        if (
            item["id"] in {"explain-feedback", "explain-standard", "explain-human", "explain-route"}
            or item["type"] == "effect:pull-request:reply"
        ):
            continue
        if any(e["source_id"] == item["id"] and e.get("source_port") == "error" for e in edges):
            continue
        name = item["id"] + "-error"
        message = (
            "The explanation could not continue at "
            + item["display_name"]
            + ". No video is ready. Check this request before posting a new command."
        )
        if item["id"] == "explain-deploy":
            message = "Redeployment was blocked by an active deployment, failed or timed out. The environment may be partially updated; video preparation has not started. Check the deployment run before retrying."
        elif item["id"] == "explain-finished":
            message = "The explanation agent completed, but its workflow completion metadata could not be saved. Check the PR thread and artifacts before retrying."
        reply(name, message)
        edge(item["id"], name, "error")
    return {
        "pack_id": "demo-video-example",
        "pack_version": "v1",
        "description": "Source-review example only. Import and adapt through the revision-gated editor after source approval; do not enable unchanged.",
        "graphs": [
            {
                "id": "demo-video-example",
                "name": "Explain a reviewed PR",
                "directory_path": directory,
                "node_ids": [n["id"] for n in nodes],
            }
        ],
        "ir": {"version": "v1", "nodes": [{"node": n} for n in nodes], "edges": edges},
    }
