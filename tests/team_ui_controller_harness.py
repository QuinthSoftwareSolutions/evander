"""Run the actual Team UI modules with a small, explicit browser environment."""

import json
from pathlib import Path


def controller_script(module: str, script: str, peers: str, methods: list[str]) -> str:
    module_uri = (
        Path(__file__).resolve().parents[1]
        / "src/evander_team_addon/ui_static"
        / f"team-ui-{module}.js"
    ).as_uri()
    factory = "createTeam" + "".join(part.capitalize() for part in module.split("-")) + "Controller"
    setup = f"""
globalThis.document = typeof document === "undefined" ? {{}} : document;
globalThis.document.querySelector ||= () => null;
globalThis.document.querySelectorAll ||= () => [];
globalThis.window = typeof window === "undefined" ? {{}} : window;
globalThis.window.location ||= {{search: "", hash: ""}};
globalThis.window.scrollTo ||= () => {{}};
const {{ {factory} }} = await import({json.dumps(module_uri)});
const controllerPeers = {peers};
const controller = {factory}(controllerPeers);
const {{ {", ".join(methods)} }} = controller;
"""
    assert script.count("// CONTROLLER") == 1
    return (
        "(async () => {\n"
        + script.replace("// CONTROLLER", setup)
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n"
    )
