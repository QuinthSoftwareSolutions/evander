from __future__ import annotations

import agent_sandbox.admin.services.vm_agent as vm_agent


def test_vm_agent_package_exports_mixins_for_direct_and_star_imports() -> None:
    from agent_sandbox.admin.services.vm_agent import VmAgentActionMixin

    namespace: dict[str, object] = {}
    exec("from agent_sandbox.admin.services.vm_agent import *", namespace)

    for name in vm_agent.__all__:
        assert name in namespace
        assert getattr(vm_agent, name) is namespace[name]

    assert namespace["VmAgentActionMixin"] is VmAgentActionMixin
