"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.effects.providers.base import EffectProviderBase

AdoUpdateWorkItemEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_update_work_item', 'AdoUpdateWorkItemEffectProvider', base=EffectProviderBase)
