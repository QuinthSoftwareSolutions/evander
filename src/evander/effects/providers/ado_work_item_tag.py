"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.effects.providers.base import EffectProviderBase

AdoWorkItemTagEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_work_item_tag', 'AdoWorkItemTagEffectProvider', base=EffectProviderBase)
AdoAddWorkItemTagEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_work_item_tag', 'AdoAddWorkItemTagEffectProvider', base=EffectProviderBase)
AdoRemoveWorkItemTagEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_work_item_tag', 'AdoRemoveWorkItemTagEffectProvider', base=EffectProviderBase)
