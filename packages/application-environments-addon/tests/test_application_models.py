import pytest
from pydantic import ValidationError
from evander_application_environments_addon.models import CreateRequest, LaunchManifest


@pytest.mark.parametrize("path", ["../recipe.json", "/tmp/recipe.json", "a/../../recipe.json"])
def test_recipe_cannot_escape_workspace(path):
    with pytest.raises(ValueError):
        CreateRequest(
            request_id="one", profile_id="web", workspace_ref="a" * 32, launch_manifest_path=path
        )


def test_application_recipe_cannot_request_privilege_or_low_ports():
    with pytest.raises(ValidationError):
        LaunchManifest(command=["node", "app"], additional_ports=[22])
    with pytest.raises(ValidationError):
        LaunchManifest(command=["node", "app"], privileged=True)
