"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.triggers.providers.base import TriggerProviderBase

AdoWorkItemCommentCreatedTriggerProvider = optional_azure_devops_class('evander_azure_devops_addon.triggers.ado_work_item_comment_created', 'AdoWorkItemCommentCreatedTriggerProvider', base=TriggerProviderBase)
