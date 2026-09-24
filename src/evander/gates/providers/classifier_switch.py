from __future__ import annotations

from evander.classification.runtime import service_for_runtime, settings_for_query
from evander.classification.switch import PreparedSwitch, execute_switch, prepare_switch
from evander.gates.providers.switch_gate import SwitchGateProvider
from evander.workflow.engine.async_gates import dispatch_async_gate


class ClassifierSwitchGateProvider(SwitchGateProvider):
    asynchronous = True

    @classmethod
    def validate_graph_node(cls, **kwargs):
        return [
            diagnostic.model_copy(
                update={
                    "node_type": "gate:classifier:switch",
                    "message": diagnostic.message.replace("gate:switch", "gate:classifier:switch"),
                }
            )
            for diagnostic in super().validate_graph_node(**kwargs)
        ]

    def execute(self, **kwargs):
        return dispatch_async_gate(**kwargs)

    def prepare(self, resolved_config):
        context = self.runtime_context
        return prepare_switch(
            service_for_runtime(context),
            settings_for_query(context),
            resolved_config,
        ).model_dump(mode="json")

    def evaluate_prepared(self, prepared, attribution):
        context = self.runtime_context
        return execute_switch(
            service_for_runtime(context),
            settings_for_query(context),
            PreparedSwitch.model_validate(prepared),
            attribution=attribution,
        )
