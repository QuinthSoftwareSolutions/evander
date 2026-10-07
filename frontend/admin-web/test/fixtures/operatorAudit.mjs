import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const permissions = ["admin.owner"];
const privileged_operations = { unlocked: true };
const directory = { id: "root", name: "root", path: "root", parent_id: null, metadata: {} };
const stamp = "2026-10-01T10:00:00Z";
export const auditCall = {
  event_id: "audit-1", tool_name: "read_repository", created_at: stamp, status: "success",
  session_id: "session-audit", agent_id: "agent-audit", vm_id: "vm-audit", approval_request_id: "approval-audit",
  workflow_run_id: "run-audit", directory_path: "root", duration_ms: 80,
  redacted_args: { path: "README.md" }, redacted_result: { bytes: 120 }, metadata: { request_kind: "read" },
};
export const artifact = {
  key: "runs/review", storage_id: "archive-audit", filename: "review.tar.gz", archive_format: "tar.gz",
  mime_type: "application/gzip", byte_count: 2048, sha256: "a".repeat(64), published_at: stamp,
  session_id: "session-audit", source_file_count: 3, source_byte_count: 1024, metadata: { purpose: "review" },
};
const worker = { worker_id: "worker-audit", name: "Audit worker", unit: "audit.service", active_state: "failed", running: false, failed: true };
const disk = { path: "/", status: "available", errors: [], total_bytes: 100000, used_bytes: 95000, free_bytes: 5000, used_percent: 95, free_percent: 5, health: "critical" };
const storageSize = { status: "available", errors: [], size_bytes: 1000, health: "ok" };

export async function operatorAuditFixture({ port = 0 } = {}) {
  const requests = [];
  const server = await createServer({
    root: fileURLToPath(new URL("../..", import.meta.url)),
    server: { host: "127.0.0.1", port }, logLevel: "error",
    plugins: [{ name: "operator-audit-fixture", configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url, "http://localhost");
        if (!url.pathname.startsWith("/api/")) return next();
        requests.push(url.pathname + url.search);
        const respond = (data, status = 200) => {
          res.statusCode = status; res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(data));
        };
        switch (url.pathname) {
          case "/api/auth/session": return respond({ authenticated: true, auth_required: false, auth_mode: "none", username: "admin", permissions, effective_permissions: permissions, can_manage_users: true, privileged_operations, available_login_methods: [] });
          case "/api/admin/state": return respond({ selected_directory_path: "root", execution_backend: "libvirt_vm", directory_scoped_config_enabled: false, permissions, effective_permissions: permissions, can_manage_users: true, privileged_operations, assistant: { enabled: false } });
          case "/api/configuration/state": return respond({ selected_directory_path: "root", execution_backend: "libvirt_vm", directory_scoped_config_enabled: false, selected_directory: { path: "root", parent_path: null }, tree: { ok: true, data: { directory, config_domains: [], children: [] }, errors: [], validation_diagnostics: [] }, tabs: [], tab_snapshots: {} });
          case "/api/admin/branding": return respond({});
          case "/api/admin/user-notifications": return respond({ unread_count: 0, notifications: [] });
          case "/api/admin/deployment-status": return respond({ active: false, stale: false });
          case "/api/admin/tool-calls": return respond({ items: [auditCall], counts: { matched: 1, returned: 1, status_counts: { success: 1 } } });
          case "/api/admin/artifacts": return respond({ items: [artifact], limit: 50, total: 1, next_cursor: null });
          case "/api/admin/artifact-spaces": return respond({ success: true, revision: 1, spaces: [], count: 0 });
          case "/api/admin/artifacts/runs%2Freview": return respond(artifact);
          case "/api/workers": return respond({ workers: [worker] });
          case "/api/admin/agents": return respond({ items: [], directory_path: "root", direct_only: false });
          case "/api/admin/approvals": return respond({ items: [], health: { total: 0, pending: 0, approved: 0, denied: 0, expired: 0 } });
          case "/api/admin/sessions": return respond({ items: [], counts: { total: 0, active: 0, expired: 0 }, status_ids: { active: [], expired: [] } });
          case "/api/admin/sessions/catalog": return respond({ tool_options: [], resume_mode_options: [], renew_extension_presets: [] });
          case "/api/workflow-editor/runs": return respond({ worker_id: "worker-audit", generated_at: stamp, runs: [], node_activity: {} });
          case "/api/workflow-editor/runs/stream": res.setHeader("Content-Type", "text/event-stream"); return res.end(": fixture\n\n");
          case "/api/admin/reports/workflow-graph-activity": return respond({ graphs: [], period_start: stamp, period_end: stamp });
          case "/api/admin/release-info": return respond({ version: "0.76.3", news: null, changelog: null, missing_reasons: ["No release news"] });
          case "/api/admin/capacity-health": return respond({
            control_plane: { disk, storage: { total: storageSize, areas: [] } },
            sessions: { total_sessions: 0, total_agents: 0, storage: { measured_total: storageSize, measured_session_count: 0, largest_sessions: [], oldest_sessions: [], missing_telemetry: [] } },
            workers: { entries: [], total_workers: 0, reachable_workers: 0, telemetry_available_workers: 0 },
            queue: { status: "available", health: "ok", pending_count: 0, inflight_count: 0, oldest_age_seconds: null, errors: [] },
            prompts: { status: "available", health: "ok", unread_count: 0, errors: [] }, generated_at: stamp,
          });
          case "/api/admin/reports/inference-costs": return respond({ error: "No pricing data in this fixture" }, 501);
          default: if (url.pathname.startsWith("/api/admin/branding/assets/")) { res.statusCode = 204; return res.end(); }
            return respond({ error: "Not provided by this fixture" }, 404);
        }
      });
    } }],
  });
  await server.listen();
  return { server, requests, url: `http://127.0.0.1:${server.httpServer.address().port}` };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const fixture = await operatorAuditFixture({ port: 5179 });
  console.log(fixture.url);
}
