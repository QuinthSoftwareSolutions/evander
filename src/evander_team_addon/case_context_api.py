"""Operator APIs for context detail, immutable captures and revisioned recipes."""

from starlette.responses import JSONResponse

from .services.case_context_service import (
    CaseContextService,
    builtin_views,
    directory,
    effective_view,
    within,
)


def case_context_routes(context, repository, route):
    from .api import (
        ADMIN_OWNER_PERMISSION,
        _auth_can,
        _read_payload,
        _expected_revision,
        _operator_actor_from_request,
        _can_view_all_team_chats,
        _operator_identity_from_request,
    )

    def scoped(request, payload=None):
        values = payload or request.query_params
        state = repository.read()
        case = state.cases.get(values.get("case_id") or request.path_params.get("case_id"))
        project = state.projects.get(case.project_id) if case else None
        path = directory(
            values.get("directory_path") or (project.directory_path if project else "root")
        )
        if not _auth_can(context.auth, request, ADMIN_OWNER_PERMISSION, directory_path=path):
            raise PermissionError("Case context detail requires directory owner permission")
        # Directory ownership is not permission to inspect another operator's private chat.
        if not _can_view_all_team_chats(context.auth, request):
            user_id = _operator_identity_from_request(context.auth, request)["user_id"]
            state.conversations = {
                key: c for key, c in state.conversations.items() if c.operator_user_id == user_id
            }
        return state, path

    def selection(payload):
        case_ids = payload.get("case_ids") or []
        if not isinstance(case_ids, list):
            raise ValueError("case_ids must be an array")
        if payload.get("case_id"):
            case_ids = [*case_ids, payload["case_id"]]
        return dict(
            case_ids=case_ids,
            task_id=payload.get("task_id"),
            release_package_id=payload.get("release_package_id"),
            view_id=payload.get("view_id") or "case-overview",
            view_version=int(payload["view_version"]) if payload.get("view_version") else None,
            cursor=int(payload.get("cursor") or 0),
        )

    async def get_context(request):
        state, path = scoped(request)
        payload = dict(request.query_params)
        payload["case_id"] = request.path_params["case_id"]
        return JSONResponse(
            CaseContextService(repository).assemble(
                state=state,
                directory_path=path,
                **selection(payload),
            )
        )

    async def get_views(request):
        state, path = scoped(request)
        names = set(builtin_views()) | {
            v.view_id for v in state.case_context_views.values() if within(path, v.directory_path)
        }
        return JSONResponse(
            {
                "revision": state.revision,
                "directory_path": path,
                "views": [
                    effective_view(state, view_id=name, directory_path=path).model_dump(mode="json")
                    for name in sorted(names)
                ],
                "history": [
                    v.model_dump(mode="json")
                    for v in state.case_context_views.values()
                    if within(path, v.directory_path)
                ],
            }
        )

    async def get_evidence(request):
        state, path = scoped(request)
        return JSONResponse(
            CaseContextService(repository).evidence_page(
                state=state,
                directory_path=path,
                case_ids=[request.path_params["case_id"]],
                section=request.query_params.get("section"),
                view_id=request.query_params.get("view_id") or "case-overview",
                view_version=int(request.query_params["view_version"])
                if request.query_params.get("view_version")
                else None,
                cursor=int(request.query_params.get("cursor") or 0),
            )
        )

    async def put_view(request):
        payload = await _read_payload(request)
        _, path = scoped(request, payload)
        return JSONResponse(
            CaseContextService(repository).save_view(
                directory_path=path,
                values=payload["view"],
                expected_revision=_expected_revision(payload),
                created_by=_operator_actor_from_request(context.auth, request),
            )
        )

    async def preview_view(request):
        from .case_context_models import CaseContextView

        payload = await _read_payload(request)
        state, path = scoped(request, payload)
        values = payload["view"]
        view = CaseContextView(**values, id="preview", directory_path=path, version=1)
        # A preview uses only a private in-memory state and creates no version or snapshot.
        state.case_context_views = {
            k: v for k, v in state.case_context_views.items() if v.view_id != view.view_id
        }
        state.case_context_views[view.id] = view
        selected = selection(payload)
        selected["view_id"] = view.view_id
        return JSONResponse(
            CaseContextService(repository).assemble(
                state=state,
                directory_path=path,
                **selected,
            )
        )

    async def create_snapshot(request):
        payload = await _read_payload(request)
        filtered, path = scoped(request, payload)
        selected = selection(payload)
        selected.pop("cursor")
        service = CaseContextService(repository)
        saved = None

        def mutate(state):
            nonlocal saved
            # Apply the same chat projection used by preview, without deleting canonical chat.
            # The shallow copy also shares this transaction's staged snapshot additions.
            projected = state.model_copy(
                update={"conversations": filtered.conversations}, deep=False
            )
            saved = service.snapshot_in_state(
                projected,
                request_id=f"operator:{_operator_actor_from_request(context.auth, request)}:{payload['request_id']}",
                directory_path=path,
                **selected,
            )

        updated = repository.update(mutate, expected_revision=_expected_revision(payload))
        return JSONResponse(
            {"snapshot_id": saved.id, "context": saved.context, "revision": updated.revision},
            status_code=201,
        )

    async def get_snapshot(request):
        snapshot_id = request.path_params["snapshot_id"]
        snapshot = repository.snapshots.get(snapshot_id)
        if not snapshot:
            return JSONResponse({"error": "snapshot unavailable"}, status_code=404)
        _, path = scoped(request, {"directory_path": snapshot.directory_path})
        if (
            not _can_view_all_team_chats(context.auth, request)
            and snapshot.evidence["conversation"]
        ):
            # Snapshots cannot retroactively declassify captured private dialogue.
            permitted, _ = scoped(request, {"directory_path": path})
            if any(
                r["entity_type"] == "chat_message"
                and r["content"].get("conversation_id") not in permitted.conversations
                for r in snapshot.evidence["conversation"]
            ):
                raise PermissionError("snapshot includes another operator's private chat")
        return JSONResponse(
            CaseContextService(repository).get_snapshot(
                snapshot_id,
                directory_path=path,
                section=request.query_params.get("section"),
                cursor=int(request.query_params.get("cursor") or 0),
                limit=int(request.query_params.get("limit") or 20),
            )
        )

    async def list_snapshots(request):
        state, path = scoped(request)
        case_id = request.path_params["case_id"]
        if case_id not in state.cases:
            return JSONResponse({"error": "case unavailable"}, status_code=404)
        return JSONResponse(
            repository.snapshots.list_for_case(
                case_id,
                directory_path=path,
                cursor=int(request.query_params.get("cursor") or 0),
            )
        )

    async def backfill_notes(request):
        payload = await _read_payload(request)
        scoped(request, {"directory_path": "root"})
        dry_run = payload.get("dry_run", True)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        return JSONResponse(
            CaseContextService(repository).backfill_notes(
                dry_run=dry_run,
                expected_revision=_expected_revision(payload) if not dry_run else None,
            )
        )

    return [
        route("/cases/{case_id}/context", get_context, methods=["GET"]),
        route("/cases/{case_id}/context/evidence", get_evidence, methods=["GET"]),
        route("/cases/{case_id}/context-snapshots", list_snapshots, methods=["GET"]),
        route("/case-context/views", get_views, methods=["GET"]),
        route("/case-context/views", put_view, methods=["PUT"], require_privileged=False),
        route("/case-context/preview", preview_view, methods=["POST"], require_privileged=False),
        route(
            "/case-context/snapshots", create_snapshot, methods=["POST"], require_privileged=False
        ),
        route("/case-context/snapshots/{snapshot_id}", get_snapshot, methods=["GET"]),
        route(
            "/case-context/notes/backfill",
            backfill_notes,
            methods=["POST"],
            require_privileged=False,
        ),
    ]
