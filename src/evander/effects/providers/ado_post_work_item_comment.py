"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.effects.providers.base import EffectProviderBase

AdoPostWorkItemCommentEffectProvider = optional_azure_devops_class('evander_azure_devops_addon.effects.ado_post_work_item_comment', 'AdoPostWorkItemCommentEffectProvider', base=EffectProviderBase)
