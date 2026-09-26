// Synthetic records for the standalone add-on design and context checks.
const timestamp = "2026-09-22T10:00:00Z";
export const addonDesignResponses = {
  "/api/addons/bokio-addon/health": { status: "ready", settings: { company_id: "example-company" } },
  "/api/addons/bokio-addon/company": { name: "Example Company" },
  "/api/addons/bokio-addon/prepared-actions": { actions: [
    { action_id: "invoice-review", kind: "supplier_invoice", status: "pending_approval", note: "September hosting invoice", source: "accounting", created_at: timestamp, updated_at: timestamp, evidence_ids: ["invoice.pdf"], payload: { supplier: "Example Hosting", amount: 1250, currency: "SEK" } },
    { action_id: "invoice-recovery", kind: "supplier_invoice", status: "needs_attention", note: "Confirm the recorded outcome", source: "accounting", created_at: timestamp, updated_at: timestamp, payload: { amount: 800, currency: "SEK" } },
  ] },
  "/api/addons/bokio-addon/operations": { operations: [{ operation_key: "hosting-invoice", session_id: "accounting-session", status: "completed", updated_at: timestamp, attempt_count: 1 }] },
  "/api/addons/mssql-seed-addon/settings": {
    sidecars: [{ sidecar_id: "worker-a", base_url: "http://worker-a:11990", token_env: "SEED_TOKEN", restore_timeout_seconds: 3600, description: "Development databases" }],
    seeds: [{ seed_alias: "app-demo", sidecar_id: "worker-a", backup_path: "/var/lib/evander/mssql-seeds/demo.bak", description: "Approved demo dataset" }],
  },
  "/api/addons/mssql-seed-addon/databases": { databases: [{ session_id: "session-demo", logical_name: "app", seed_alias: "app-demo", database_name: "session_demo_app", reset_count: 2, last_reset_at: timestamp }] },
  "/api/addons/miro-addon/connections": { connections: [{ config: { id: "design", name: "Design workspace", directory_path: "root", redirect_uri: "http://localhost/api/addons/miro-addon/oauth/callback", daily_call_budget: 100, enabled: true, allow_workflows: true, allow_create_boards: false, boards: [{ board_id: "example-board", name: "Architecture review", writable: true }] }, revision: 3, connected: true, calls_today: 4 }] },
  "/api/addons/miro-addon/connections/design/activity": { subscriptions: [{ subscription_id: "architecture-review", checked_at: 1790071200 }], operations: [{ operation_id: "diagram-update", operation: "update_items", status: "uncertain", actor: "Design agent" }] },
  "/api/addons/figma-addon/settings": { revision: 3, settings: {
    connections: { design: { client_id: "example-client", client_secret_env: "FIGMA_CLIENT_SECRET", redirect_uri: "http://localhost/api/addons/figma-addon/oauth/callback", rest_token_env: "FIGMA_REST_TOKEN", rest_token_kind: "personal" } },
    targets: { admin: { name: "Admin Web", connection_id: "design", file_key: "example-file", access: ["read", "comment"], edit_roots: [] } },
  }, connections: { design: { connected: true, authoring_ready: true } } },
  "/api/addons/figma-addon/history": { snapshots: [], operations: [{ operation_id: "design-review", request: { operation: "update_design" }, status: "uncertain", actor: "Design agent", created_at: timestamp }] },
  "/api/addons/skatteverket-addon/connection-status": { configured: true, ready: true, connection_id: "default", environment: "sandbox", base_url_configured: true, submission_endpoint_configured: false, missing_secret_refs: [], enabled_api_names: ["vat"] },
  "/api/addons/skatteverket-addon/catalog": { apis: [{ name: "vat", display_name: "VAT declarations", version: "1", implemented_operations: ["prepare", "validate", "submit"] }] },
  "/api/addons/skatteverket-addon/state": {
    connections: [{ connection_id: "default", display_name: "Tax reporting", environment: "sandbox", enabled: true, enabled_api_names: ["vat"], client_id_env: "SKV_CLIENT_ID", client_secret_env: "SKV_CLIENT_SECRET" }],
    drafts: [{ draft_id: "vat-september", api_name: "vat", status: "prepared", revision: 2 }],
    recent_submissions: [{ submission_id: "vat-august", api_name: "vat", status: "accepted", created_at: timestamp }],
  },
};
