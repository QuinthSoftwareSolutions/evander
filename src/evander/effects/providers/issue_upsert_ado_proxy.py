"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.effects.providers.base import EffectProviderBase

IssueUpsertAdoProxyEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.issue_upsert_ado_proxy', 'IssueUpsertAdoProxyEffectProvider', base=EffectProviderBase)
