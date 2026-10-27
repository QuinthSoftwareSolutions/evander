"""Optional narrated PR videos; no implicit installation or live configuration."""

from evander.addons import AddonRegistration

from .narration import NarrationConnectorDriver


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id="demo-video-addon",
        display_name="Demo videos",
        connector_drivers={"demo_video_narration": NarrationConnectorDriver},
        metadata={"description": "Host-owned narration and isolated browser video helpers."},
    )
