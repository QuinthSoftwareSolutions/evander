from __future__ import annotations

from collections.abc import Mapping

from agent_sandbox.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from agent_sandbox.effects.providers.agent_prompt import AgentPromptEffectProvider
from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.effects.providers.bash_script import BashScriptEffectProvider
from agent_sandbox.effects.providers.git_promote_agent_branch import GitPromoteAgentBranchEffectProvider
from agent_sandbox.effects.providers.issue_update_state import IssueUpdateStateEffectProvider
from agent_sandbox.effects.providers.pr_create import PrCreateEffectProvider
from agent_sandbox.effects.providers.session_update_metadata import SessionUpdateMetadataEffectProvider
from agent_sandbox.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from agent_sandbox.effects.providers.vm_create import VmCreateEffectProvider
from agent_sandbox.effects.providers.vm_delete_files import VmDeleteFilesEffectProvider
from agent_sandbox.effects.providers.vm_exec import VmExecEffectProvider
from agent_sandbox.effects.providers.vm_start import VmStartEffectProvider
from agent_sandbox.effects.providers.vm_stop import VmStopEffectProvider
from agent_sandbox.gates.providers.await_agent import AwaitAgentGateProvider
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.gates.providers.filter_gate import FilterGateProvider
from agent_sandbox.gates.providers.for_gate import ForGateProvider
from agent_sandbox.gates.providers.map_gate import MapGateProvider
from agent_sandbox.gates.providers.merge_gate import MergeGateProvider
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.queries.providers.git_pr_note import GitPrNoteQueryProvider
from agent_sandbox.queries.providers.issue_get import IssueGetQueryProvider
from agent_sandbox.queries.providers.session_context import SessionContextQueryProvider
from agent_sandbox.queries.providers.vm_run_state import VmRunStateQueryProvider


GATE_PLUGIN_CLASSES: Mapping[str, type[GateProviderBase]] = {
    "gate:await-agent": AwaitAgentGateProvider,
    "gate:filter": FilterGateProvider,
    "gate:for": ForGateProvider,
    "gate:map": MapGateProvider,
    "gate:merge": MergeGateProvider,
}

EFFECT_PLUGIN_CLASSES: Mapping[str, type[EffectProviderBase]] = {
    "effect:vm:create": VmCreateEffectProvider,
    "effect:vm:start": VmStartEffectProvider,
    "effect:vm:stop": VmStopEffectProvider,
    "effect:agent:prompt": AgentPromptEffectProvider,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectProvider,
    "effect:bash:script": BashScriptEffectProvider,
    "effect:vm:exec": VmExecEffectProvider,
    "effect:vm:copy-files": VmCopyFilesEffectProvider,
    "effect:vm:delete-files": VmDeleteFilesEffectProvider,
    "effect:issue:update-state": IssueUpdateStateEffectProvider,
    "effect:session:update-metadata": SessionUpdateMetadataEffectProvider,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectProvider,
    "effect:pr:create": PrCreateEffectProvider,
}

QUERY_PLUGIN_CLASSES: Mapping[str, type[QueryProviderBase]] = {
    "query:vm:run-state": VmRunStateQueryProvider,
    "query:git:pr-note": GitPrNoteQueryProvider,
    "query:session-context": SessionContextQueryProvider,
    "query:issue:get": IssueGetQueryProvider,
}
