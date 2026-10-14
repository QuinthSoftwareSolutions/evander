"""Compatibility report defaults and lazy access to the Azure DevOps add-on."""

from evander.optional_integrations import require_azure_devops_module
import re
DEFAULT_DONE_STATES = ("Done", "Closed", "Completed")
DEFAULT_IN_PROGRESS_STATES = ("In Progress", "Active")
DEFAULT_EFFORT_FIELDS = (
    "Microsoft.VSTS.Scheduling.Effort",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
)
DEFAULT_ELAPSED_TIME_MODE = "swedish_work_time"
MANUAL_ELAPSED_TIME_MODES = frozenset({"manual", "manual_override"})
DEFAULT_WORKDAY_START = "08:00"
DEFAULT_WORKDAY_END = "17:00"
SWEDEN_TIMEZONE = "Europe/Stockholm"
_STATE_SEPARATOR_PATTERN = re.compile(r"[\s_-]+")



def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(require_azure_devops_module('evander_azure_devops_addon.reports'), name)


def build_ado_daily_status_report_data(*args, **kwargs):
    return require_azure_devops_module("evander_azure_devops_addon.reports").build_ado_daily_status_report_data(*args, **kwargs)
