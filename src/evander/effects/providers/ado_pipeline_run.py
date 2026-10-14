"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.effects.providers.base import EffectProviderBase

AdoPipelineRunEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_pipeline_run', 'AdoPipelineRunEffectProvider', base=EffectProviderBase)
