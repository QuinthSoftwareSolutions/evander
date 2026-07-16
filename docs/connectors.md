# Connectors

Connectors are named, host-owned capabilities that let an agent session retrieve approved files or
invoke narrowly defined REST operations without learning endpoint credentials or choosing an
arbitrary destination. They complement the HTTP target relay: the relay exposes an allowlisted
network path with guest-managed authentication, while a connector exposes a fixed semantic
operation and can inject a host service identity.

## Capability Model

A connector definition has five relevant layers:

- the stable connector id and operator-facing description;
- a driver (`rest`, `filesystem`, or an enabled add-on driver);
- an optional host-side authentication profile;
- one or more fixed, JSON-Schema-described operations; and
- an explicit per-session grant listing exactly which operations the session may use.

The MCP gateway exposes only two generic tools:

- `list_connectors` returns the names, descriptions, input schemas, result modes, and approval
  requirements for operations granted to the calling session;
- `call_connector` invokes one granted connector operation with schema-validated arguments.

The discovery result does not contain base URLs, filesystem roots, environment variable names, or
credential values. A session must allow the generic MCP tools *and* hold an operation grant. A
grant alone does not make either MCP tool visible.

## Directory Configuration

Connector configuration is the `connectors` directory domain, stored at
`runtime/directory-config/root/.../connectors.json`. Maps inherit through the normal directory
deep-merge chain. A child may override selected connector or operation fields, or disable an
inherited connector with:

```json
{
  "connectors": {
    "documents": {
      "disabled": true
    }
  }
}
```

Use Admin Web **Connectors** for effective definitions, auth availability, revision-checked edits,
and health tests. The CLI equivalents are:

```bash
evander-cli connectors list --directory root
evander-cli connectors show documents --directory root
evander-cli connectors validate --directory root
evander-cli connectors test documents --directory root
```

Saving and health tests require privileged operations to be unlocked. A health operation must be
an unapproved read operation whose configured `health_arguments` satisfy its input schema.

## REST Connector Example

REST destinations, methods, paths, query mappings, fixed headers, and body placement are operator
configuration. Agents can supply only values declared by an operation input schema.

```json
{
  "auth_profiles": {
    "documents-service": {
      "type": "oauth2_client_credentials",
      "token_url": "https://identity.example.com/oauth2/token",
      "client_id_env": "DOCUMENTS_CLIENT_ID",
      "client_secret_env": "DOCUMENTS_CLIENT_SECRET",
      "scopes": ["documents.read"]
    }
  },
  "connectors": {
    "documents": {
      "display_name": "Documents",
      "description": "Approved document service",
      "driver": "rest",
      "auth_profile": "documents-service",
      "config": {
        "base_url": "https://documents.example.com"
      },
      "health_operation": "get_document",
      "health_arguments": {"id": "health"},
      "operations": {
        "get_document": {
          "description": "Retrieve one approved document",
          "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1}},
            "required": ["id"],
            "additionalProperties": false
          },
          "access": "read",
          "requires_approval": false,
          "result_mode": "artifact",
          "timeout_seconds": 30,
          "max_response_bytes": 10485760,
          "config": {
            "method": "GET",
            "path": "/documents/{id}",
            "follow_same_origin_redirects": false
          }
        }
      }
    }
  }
}
```

Path-template arguments are percent-encoded. Query configuration maps argument names to fixed
query parameter names. `json_body_argument` selects one schema-declared argument as the JSON body.
Agents cannot submit request headers. Redirects are rejected by default; when enabled, only up to
three same-origin redirects are followed. Responses are streamed into a bounded buffer and fail
when `max_response_bytes` is exceeded.

`result_mode` is one of:

- `json`: parsed JSON returned inline, with a maximum inline size of 1 MiB;
- `text`: decoded text returned inline, with the same maximum;
- `artifact`: bytes staged in the host communication-artifact store and returned as an opaque
  artifact handle.

Copying an artifact into an agent workspace remains a separate policy decision through the
existing communication-artifact copy tool and file-receive policy.

## Filesystem Connector Example

Filesystem connectors expose only regular files below one approved absolute root. Resolved paths,
including symlinks, must remain below that root. Results are always staged as artifacts.

```json
{
  "connectors": {
    "release-reports": {
      "display_name": "Release reports",
      "description": "Approved generated release reports",
      "driver": "filesystem",
      "config": {"root": "/srv/evander/reports"},
      "operations": {
        "get_report": {
          "description": "Retrieve one release report",
          "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": false
          },
          "access": "read",
          "requires_approval": false,
          "result_mode": "artifact",
          "config": {"path": "{name}"}
        }
      }
    }
  }
}
```

## Authentication

Authentication profiles reference host environment variables; connector JSON never stores secret
values. Supported profile types are:

- `none`;
- `bearer_env` with `token_env`;
- `api_key_header_env` with `token_env` and a non-reserved `header_name`;
- `basic_env` with `username_env` and `password_env`;
- `oauth2_client_credentials` with `token_url`, `client_id_env`,
  `client_secret_env`, optional `scopes`/`audience`, and either
  `client_secret_basic` or `client_secret_post`.

OAuth tokens are cached per directory and profile, refreshed before expiry, and fetched under a
per-profile async lock so concurrent connector calls do not stampede the token endpoint. One 401
retry is allowed only for read operations using OAuth client credentials; write operations are
never replayed automatically. Auth errors returned to agents are deliberately generic.

This first implementation models application/service identities. User-delegated OAuth belongs in
a separate credential-binding design because user consent, refresh-token custody, revocation, and
session-to-user delegation introduce a different authority boundary.

## Approvals, Audit, And Session Grants

Every operation declares both `access` (`read` or `write`) and `requires_approval` explicitly.
When approval is required, the normal MCP approval mechanism binds the exact connector id,
operation, and arguments before the driver performs authentication or network/filesystem I/O.
Directory policy may additionally require approval for the generic `call_connector` tool.

Connector calls use the existing tool-call audit path and record `connector_id` plus
`connector_operation`. Admin and CLI health tests also emit connector-test audit events. Arguments
and results pass through the normal redaction logic; authentication headers and token responses are
never added to tool arguments or results.

Session manifests store grants as:

```json
{
  "allowed_tools": ["list_connectors", "call_connector"],
  "connector_grants": {
    "documents": ["get_document"]
  }
}
```

Admin Web validates grants against the session's effective directory configuration when creating
or updating VM and container-worker agents. Workflow `agent:create` and `vm:create` effects accept
the same `connector_grants` field.

## Custom Drivers

Configuration covers ordinary REST and approved-root file retrieval. An unusual protocol or
multi-step integration can be implemented as a small add-on driver without adding a new MCP tool.
Register a driver name in `AddonRegistration.connector_drivers`; the value may be a driver instance
or a zero-argument factory. A driver implements:

```python
class ConnectorDriver(Protocol):
    def validate_definition(self, definition: ConnectorDefinition) -> None: ...

    async def invoke(
        self,
        *,
        context: ConnectorCallContext,
        connector_id: str,
        definition: ConnectorDefinition,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult: ...
```

Drivers receive already resolved host auth headers and a host artifact store, but they remain
responsible for validating their fixed configuration and enforcing operation bounds. Enabling or
disabling an add-on that contributes connector drivers requires a gateway restart.
