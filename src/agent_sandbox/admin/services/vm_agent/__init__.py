from __future__ import annotations

_VM_AGENT_EXPORTS = {
    "VmAgentActionMixin": (".agent_actions", "VmAgentActionMixin"),
    "VmAgentChatAssistantMixin": (".chat_assistant", "VmAgentChatAssistantMixin"),
    "VmAgentContainerBackendMixin": (".container_backend", "VmAgentContainerBackendMixin"),
    "VmAgentContainerSessionMixin": (".container_sessions", "VmAgentContainerSessionMixin"),
    "VmAgentCreateMixin": (".agent_create", "VmAgentCreateMixin"),
    "VmAgentInventoryMixin": (".inventory", "VmAgentInventoryMixin"),
    "VmAgentLifecycleMixin": (".vm_lifecycle", "VmAgentLifecycleMixin"),
}

__all__ = sorted(_VM_AGENT_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute_name = _VM_AGENT_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
