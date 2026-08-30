# Alexa Agent Bridge

The Alexa agent bridge is an operator-configured composition of existing Evander primitives. It
does not require an Evander add-on:

- `trigger:http:webhook` accepts the signed question;
- `gate:filter` and `gate:dedupe` validate and deduplicate the request;
- `effect:agent:enqueue-prompt` durably queues it for one fixed session; and
- a host-authenticated REST connector lets that agent submit its answer to the Alexa mailbox.

The Amazon-facing skill, callback API, durable mailbox, and proactive notification sender belong
in a separate personal repository. Evander never receives Login with Amazon credentials.

## Trust boundaries

The public request may contain only `request_id`, `alexa_user_id`, `question`, and `submitted_at`.
The workflow target is fixed in operator configuration; never bind an agent ID, session ID,
directory, connector name, callback URL, or authorization setting from the request body.

The Alexa service must allowlist its user before signing a request. Evander then authenticates the
service with its webhook HMAC, validates the bounded envelope, and deduplicates the immutable
request ID for 30 days. The question remains untrusted prompt content and does not grant Team or
project mutation authority.

The return connector has one exact destination, one `POST /replies` operation, and a host-injected
bearer token. Its token is never passed to the agent or stored in the connector JSON.

## Install the workflow

1. Copy `config/workflow-packs/alexa-agent-bridge.v1-example.json` into an operator-owned live pack
   through Admin Web **Workflow**. Do not overwrite a live pack file directly.
2. Replace `REPLACE_WITH_FIXED_AGENT_SESSION_ID` with the persistent target session. For the
   personal Truman installation this is the session currently backing Truman, not a value supplied
   by Alexa.
3. Keep the webhook slug random or host-private if the public proxy cannot expose the example
   `alexa-truman` slug unchanged. The Lambda URL and workflow slug must match exactly.
4. Add the pack to the trigger-orchestrator `workflow_packs` list using the editor's revision and
   lock behavior, save, relock, and read it back.
5. Load the AWS-generated webhook secret as `ALEXA_TRUMAN_WEBHOOK_SECRET` in the Admin Web service
   environment and restart `evander-admin-web.service`.
6. Expose only `POST /api/workflow-webhooks/<slug>` through the public HTTPS reverse proxy. Do not
   expose the whole Admin Web application merely for this integration.

The webhook sender must serialize the JSON body once and sign those exact bytes. Evander's HMAC
material is:

```text
<unix-timestamp>.<canonical-query-json>.<raw-body-bytes>
```

Canonical query JSON uses sorted keys, compact separators, and UTF-8. With no query parameters,
the signed prefix is `<unix-timestamp>.{}.`. Send the result as
`X-Evander-Signature-256: sha256=<lowercase-hex>` together with `X-Evander-Timestamp`. Timestamps
outside the five-minute acceptance window are rejected.

## Install the reply connector

1. In Admin Web **Connectors**, merge
   `config/connectors/alexa-reply.json.example` into the fixed agent's Evander directory.
2. Replace the example `base_url` with the AWS stack's `ReplyCallbackUrl` origin and stage. Keep
   the operation path `/replies` so the combined URL matches the stack output exactly.
3. Load the AWS-generated callback secret as `ALEXA_REPLY_CALLBACK_TOKEN` in the MCP gateway
   environment, then restart `evander-gateway.service` and `evander-admin-web.service`.
4. Validate the effective connector configuration. This write-only connector intentionally has no
   health operation because a health call would create mailbox state.
5. Edit the fixed session policy so `allowed_tools` contains `list_connectors` and
   `call_connector`, and `connector_grants` includes:

   ```json
   {
     "alexa-reply": ["submit"]
   }
   ```

6. Confirm with `evander-cli connectors show alexa-reply --directory <directory>` that the
   connector is available. The output must not contain the callback token.

The connector is deliberately configured with `requires_approval: false`: the fixed workflow
expects the agent to return a response after Alexa has already ended its request. Its authority is
bounded by the exact callback origin, path, JSON schema, and personal mailbox token.

## Verification

Before enabling the public route:

1. Send a locally signed request and confirm that a malformed signature, stale timestamp, changed
   query, changed body, or unknown slug is rejected.
2. Confirm that replaying the same `request_id` does not prompt the agent twice.
3. From the fixed session, list connectors and verify that only `alexa-reply.submit` is granted for
   this integration.
4. Submit a test callback and verify that the bearer token is injected by the host, the response is
   accepted, and neither tool audit nor agent output contains the token.
5. Ask two Alexa questions before either completes, then confirm that both replies are retained and
   spoken oldest-first.

The agent is responsible for calling the connector. If that behavioral contract proves
insufficiently reliable, the next step is an Alexa communication add-on backed by durable Team
Chat replies. That stronger integration is intentionally outside this minimal version.
