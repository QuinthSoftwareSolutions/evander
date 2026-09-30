"""Opt-in real WordPress/PHP/MariaDB tests; resources are removed even on failure.

EVANDER_RUN_WORDPRESS_INTEGRATION=1 pytest -q tests/test_wordpress_integration.py
Images must be available locally. The fixture uses an internal Docker network and
synthetic data only; no production website, credential or deployment is involved.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import base64
from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import time
import tempfile
from uuid import uuid4
import zipfile

import httpx
import pytest
from evander_wordpress_addon.ssh_helper import Helper
from evander_wordpress_addon.deployment import PipelineAdapter
from evander_wordpress_addon.transport import Client

pytestmark = pytest.mark.skipif(
    os.environ.get("EVANDER_RUN_WORDPRESS_INTEGRATION") != "1",
    reason="requires the opt-in Docker WordPress fixture",
)
ROOT = Path(__file__).resolve().parents[1]


def docker(*args, timeout=90):
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Docker fixture operation failed: " + result.stderr[-1000:])
    return result.stdout.strip()


class ContainerClient:
    def __init__(self, container, *, headers=None, auth=None):
        self.container, self.headers, self.auth = container, headers or {}, auth

    def request(self, method, path, *, json=None, data=None):
        import json as codec
        from urllib.parse import urlencode

        headers = dict(self.headers)
        body = None
        if json is not None:
            headers["Content-Type"] = "application/json"
            body = codec.dumps(json)
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(data)
        result = subprocess.run(
            ["docker", "exec", "-i", self.container, "php", "/fixtures/request.php"],
            input=codec.dumps(
                {
                    "path": path,
                    "method": method,
                    "headers": headers,
                    "auth": self.auth,
                    "body": body,
                }
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("Fixture HTTP request failed")
        value = codec.loads(result.stdout)
        return httpx.Response(value["status"], text=value["body"])

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture(scope="module")
def wordpress():
    prefix = "evander-wp-test-" + uuid4().hex[:10]
    db, wp, network = prefix + "-db", prefix + "-wp", prefix + "-net"
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    releases = Path(temporary.name) / "theme"
    initial = releases / "releases/initial"
    initial.mkdir(parents=True)
    for path in (ROOT / "tests/fixtures/wordpress/theme").iterdir():
        (initial / path.name).write_bytes(path.read_bytes())
    (releases / "current").symlink_to("releases/initial")
    try:
        docker("network", "create", "--internal", network)
        docker(
            "run",
            "-d",
            "--name",
            db,
            "--network",
            network,
            "--network-alias",
            "database",
            "-e",
            "MARIADB_ROOT_PASSWORD=fixture-root-password",
            "-e",
            "MARIADB_DATABASE=wordpress",
            "-e",
            "MARIADB_USER=wordpress",
            "-e",
            "MARIADB_PASSWORD=fixture-db-password",
            "mariadb:11",
        )
        docker(
            "run",
            "-d",
            "--name",
            wp,
            "--network",
            network,
            "-e",
            "WORDPRESS_DB_HOST=database",
            "-e",
            "WORDPRESS_DB_USER=wordpress",
            "-e",
            "WORDPRESS_DB_PASSWORD=fixture-db-password",
            "-e",
            "WORDPRESS_DB_NAME=wordpress",
            "-e",
            "WORDPRESS_CONFIG_EXTRA=$_SERVER['HTTPS']='on'; define('WP_ENVIRONMENT_TYPE','local'); define('WP_HOME','https://design.example.test'); define('WP_SITEURL','https://design.example.test');",
            "-v",
            str(releases) + ":/theme-releases:ro",
            "-v",
            str(ROOT / "src/evander_wordpress_addon/companion") + ":/companion:ro",
            "-v",
            str(ROOT / "tests/fixtures/wordpress") + ":/fixtures:ro",
            "wordpress:php8.3-apache",
        )
        # Bounded readiness, independent of a browser's networkidle heuristics.
        for attempt in range(60):
            ready = subprocess.run(
                ["docker", "exec", db, "healthcheck.sh", "--connect", "--innodb_initialized"],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("MariaDB fixture did not become ready")
        for path in ["/companion/evander-wordpress.php", "/companion/resources.php"]:
            docker("exec", wp, "php", "-l", path)
        data = json.loads(docker("exec", wp, "php", "/fixtures/setup.php"))
        data["release_root"] = str(releases)
        with ContainerClient(wp, auth=("designer", data["password"])) as client:
            yield client, data, wp
    finally:
        for name in [wp, db]:
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=15)
        temporary.cleanup()


def call(client, operation, payload=None):
    response = client.request(
        "POST" if payload is not None else "GET",
        "/index.php?rest_route=/evander/v1/" + operation,
        json=payload,
    )
    return response


def inspect(client, key):
    response = call(client, "inspect", {"key": key})
    assert response.status_code == 200, response.text
    return response.json()


def prepare(client, resources):
    id = "wp_" + uuid4().hex
    rows = [
        {"key": key, "expected_version": inspect(client, key)["version"], "value": value}
        for key, value in resources.items()
    ]
    response = call(
        client,
        "prepare",
        {"change_id": id, "expires_at": int(time.time() + 3600), "resources": rows},
    )
    assert response.status_code == 200, response.text
    captured = response.json()["snapshot"]
    assert (
        call(
            client,
            "arm",
            {"change_id": id, "fence": 100, "snapshot_digest": "durably-captured-fixture"},
        ).status_code
        == 200
    )
    return id, captured


def test_real_wp_protocol_atomic_apply_replay_and_undo_preserve_other_record(wordpress):
    client, data, wp = wordpress
    discovered = call(client, "discover")
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["transactional"] and discovered.json()["browser_ready"]
    key = "post:" + str(data["post"])
    other = inspect(client, "post:" + str(data["other"]))
    before = inspect(client, key)
    id, snapshot = prepare(client, {key: {"post_title": "Published heading"}})
    payload = {"change_id": id, "fence": 100}
    assert call(client, "apply", payload).status_code == 200
    assert call(client, "apply", payload).status_code == 200
    assert inspect(client, key)["value"]["post_title"] == "Published heading"
    assert inspect(client, "post:" + str(data["other"])) == other
    response = call(client, "restore", {**payload, "snapshot": snapshot})
    assert response.status_code == 200, response.text
    assert inspect(client, key) == before
    assert call(client, "probe", {"change_id": "wp_" + uuid4().hex}).json()["restore_verified"]


def test_concurrent_compare_and_swap_allows_one_writer_and_refuses_stale_undo(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    first, snapshot1 = prepare(client, {key: {"post_title": "Writer one"}})
    second, snapshot2 = prepare(client, {key: {"post_title": "Writer two"}})

    def apply(id):
        return call(client, "apply", {"change_id": id, "fence": 100}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(apply, [first, second]))
    assert sorted(statuses) == [200, 409]
    winner = first if statuses[0] == 200 else second
    human, snapshot3 = prepare(client, {key: {"post_title": "Human edit"}})
    assert call(client, "apply", {"change_id": human, "fence": 100}).status_code == 200
    response = call(
        client,
        "restore",
        {
            "change_id": winner,
            "fence": 100,
            "snapshot": snapshot1 if winner == first else snapshot2,
        },
    )
    assert response.status_code == 409
    assert inspect(client, key)["value"]["post_title"] == "Human edit"


def test_short_browser_ticket_is_single_use_and_rejects_uncovered_writes(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    before = inspect(client, key)
    id, snapshot = prepare(client, {key: {"post_title": "Browser heading"}})
    response = call(
        client,
        "browser/ticket",
        {"change_id": id, "fence": 100, "expires_at": int(time.time() + 900)},
    )
    assert response.status_code == 200, response.text
    ticket = response.json()["ticket"]
    state = call(client, "browser/redeem", {"ticket": ticket})
    assert state.status_code == 200, state.text
    assert call(client, "browser/redeem", {"ticket": ticket}).status_code in [403, 409]
    cookies = state.json()["storageState"]["cookies"]
    assert all(cookie["secure"] and cookie["httpOnly"] for cookie in cookies)
    assert state.json()["expiresAt"] <= time.time() + 900
    cookie = "; ".join(row["name"] + "=" + row["value"] for row in cookies if row["path"] == "/")
    admin_cookie = "; ".join(
        row["name"] + "=" + row["value"] for row in cookies if row["path"].endswith("/wp-admin")
    )
    # The fixture forces HTTPS inside its isolated container. This client explicitly
    # presents the scoped cookie to exercise normal WP browser request hooks.
    with ContainerClient(client.container, headers={"Cookie": cookie}) as browser:
        response = browser.post(
            "/wp-admin/admin-ajax.php",
            data={"action": "evander_fixture_save", "id": data["post"], "title": "Browser heading"},
        )
        if response.status_code in [500, 409]:
            logs = subprocess.run(
                ["docker", "logs", wp], capture_output=True, text=True, timeout=10
            )
            diagnostic = "\n".join(
                line
                for line in logs.stderr.splitlines()
                if "PHP Fatal" in line or "Uncaught" in line or "fixture write:" in line
            )
            pytest.fail("Browser request failed: " + diagnostic)
        assert response.status_code == 200, response.text
        assert inspect(client, key)["value"]["post_title"] == "Browser heading"
        assert (
            browser.post(
                "/wp-admin/admin-ajax.php", data={"action": "evander_fixture_uncovered"}
            ).status_code
            == 409
        )
        meta = "meta:" + str(data["post"]) + ":_builder_data"
        before_meta = inspect(client, meta)
        extension = {
            "change_id": id,
            "idempotency_key": "cover-builder-data",
            "resources": [
                {
                    "key": meta,
                    "expected_version": before_meta["version"],
                    "value": [{"heading": "Builder heading"}],
                }
            ],
        }
        captured = call(client, "prepare-extend", extension)
        assert captured.status_code == 200, captured.text
        payload = {
            "action": "evander_fixture_meta",
            "id": data["post"],
            "heading": "Builder heading",
        }
        assert browser.post("/wp-admin/admin-ajax.php", data=payload).status_code == 409
        snapshot.update(captured.json()["snapshot"])
        assert (
            call(
                client,
                "arm-extend",
                {
                    "change_id": id,
                    "idempotency_key": extension["idempotency_key"],
                    "fence": 100,
                    "snapshot_digest": "durably-captured-extension",
                },
            ).status_code
            == 200
        )
        response = browser.post("/wp-admin/admin-ajax.php", data=payload)
        assert response.status_code == 200, response.text
        assert inspect(client, meta)["value"] == [{"heading": "Builder heading"}]
        assert (
            browser.post(
                "/wp-admin/admin-ajax.php",
                data={
                    "action": "evander_fixture_move_meta",
                    "id": data["post"],
                    "other": data["other"],
                },
            ).status_code
            == 409
        )
        assert inspect(client, "meta:" + str(data["other"]) + ":_builder_data")["value"] == []
    with ContainerClient(client.container, headers={"Cookie": admin_cookie}) as admin_only:
        assert admin_cookie, (
            "fixture must exercise the secure admin cookie without the logged-in cookie"
        )
        assert (
            admin_only.post(
                "/wp-admin/admin-ajax.php", data={"action": "evander_fixture_uncovered"}
            ).status_code
            == 409
        )
    renewed = call(
        client,
        "browser/ticket",
        {"change_id": id, "fence": 100, "expires_at": int(time.time() + 900)},
    )
    assert renewed.status_code == 200, renewed.text
    renewed_state = call(client, "browser/redeem", {"ticket": renewed.json()["ticket"]})
    assert renewed_state.status_code == 200, renewed_state.text
    with ContainerClient(client.container, headers={"Cookie": cookie}) as expired_browser:
        assert (
            expired_browser.post(
                "/wp-admin/admin-ajax.php",
                data={
                    "action": "evander_fixture_save",
                    "id": data["post"],
                    "title": "Revoked cookie",
                },
            ).status_code
            != 200
        )
    new_cookie = next(
        row for row in renewed_state.json()["storageState"]["cookies"] if row["path"] == "/"
    )
    with ContainerClient(
        client.container, headers={"Cookie": new_cookie["name"] + "=" + new_cookie["value"]}
    ) as renewed_browser:
        assert (
            renewed_browser.post(
                "/wp-admin/admin-ajax.php",
                data={
                    "action": "evander_fixture_save",
                    "id": data["post"],
                    "title": "Browser heading",
                },
            ).status_code
            == 200
        )
        assert (
            renewed_browser.post(
                "/wp-admin/admin-ajax.php",
                data={
                    "action": "evander_fixture_save",
                    "id": data["other"],
                    "title": "Outside manifest",
                },
            ).status_code
            == 409
        )
    assert call(client, "close", {"change_id": id}).status_code == 200
    assert (
        call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code
        == 200
    )
    assert inspect(client, key) == before
    assert inspect(client, meta) == before_meta


def test_menu_relationship_recovery_preserves_another_objects_menu(wordpress):
    client, data, _ = wordpress
    key = "relationships:" + str(data["post"]) + ":nav_menu"
    other = "relationships:" + str(data["other"]) + ":nav_menu"
    before, untouched = inspect(client, key), inspect(client, other)
    id, snapshot = prepare(
        client, {key: [{"term_taxonomy_id": data["second_menu"], "term_order": 3}]}
    )
    response = call(client, "apply", {"change_id": id, "fence": 100})
    assert response.status_code == 200, response.text
    assert inspect(client, key)["value"] == [
        {"term_taxonomy_id": str(data["second_menu"]), "term_order": "3"}
    ]
    assert inspect(client, other) == untouched
    assert (
        call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code
        == 200
    )
    assert inspect(client, key) == before
    assert inspect(client, other) == untouched


def test_design_option_undo_restores_value_and_autoload_metadata(wordpress):
    client, _, _ = wordpress
    key = "option:theme_mods_evander-fixture-theme"
    before = inspect(client, key)
    id, snapshot = prepare(client, {key: {"value": {"custom_color": "#123456"}, "autoload": "off"}})
    assert call(client, "apply", {"change_id": id, "fence": 100}).status_code == 200
    assert inspect(client, key)["value"] == {
        "value": {"custom_color": "#123456"},
        "autoload": "off",
    }
    assert (
        call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code
        == 200
    )
    assert inspect(client, key) == before


def test_browser_cookie_cannot_be_reused_for_a_different_blog(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    id, snapshot = prepare(client, {key: {"post_title": "Scoped heading"}})
    ticket = call(
        client,
        "browser/ticket",
        {"change_id": id, "fence": 100, "expires_at": int(time.time() + 900)},
    ).json()["ticket"]
    cookies = call(client, "browser/redeem", {"ticket": ticket}).json()["storageState"]["cookies"]
    logged_in = next(row for row in cookies if row["path"] == "/")
    token = logged_in["value"].split("|")[2]
    # A blog sharing this user's session store must reject a cookie marked for another blog.
    script = (
        "<?php require '/var/www/html/wp-load.php'; $m=WP_Session_Tokens::get_instance(get_user_by('login','designer')->ID); $token="
        + json.dumps(token)
        + "; $s=$m->get($token); $s['evander_blog_id']=999; $m->update($token,$s);"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", wp, "php"],
        input=script,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0
    with ContainerClient(
        wp, headers={"Cookie": logged_in["name"] + "=" + logged_in["value"]}
    ) as browser:
        assert (
            browser.post(
                "/wp-admin/admin-ajax.php",
                data={"action": "evander_fixture_save", "id": data["post"], "title": "Wrong blog"},
            ).status_code
            == 403
        )
    assert call(client, "close", {"change_id": id}).status_code == 200
    assert (
        call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code
        == 200
    )


@pytest.mark.parametrize("backend", ["ssh", "pipeline"])
def test_external_backends_restore_real_fatal_theme_when_wordpress_api_is_unavailable(
    wordpress, backend
):
    import asyncio

    client, data, wp = wordpress
    import shutil

    helper = Helper(
        {
            "components": {"theme": data["release_root"]},
            "post_switch_command": [
                shutil.which("docker"),
                "exec",
                wp,
                "apachectl",
                "-k",
                "graceful",
            ],
        }
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("style.css", "/* Theme Name: Synthetic Recovery Theme */")
        archive.writestr("functions.php", "<?php definitely_missing_function_for_fixture();")
        archive.writestr("index.php", "<?php echo 'Broken fixture';")
    artifact = stream.getvalue()

    async def auth():
        return {}

    def protocol(request):
        args = json.loads(request.content)
        args.update(protocol_version=1, action=request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=helper.execute(args))

    pipeline = PipelineAdapter(
        Client(
            "https://hosting.example.test",
            auth,
            transport_factory=lambda origin: httpx.MockTransport(protocol),
        )
    )

    def operation(action, **payload):
        args = {
            "operation_id": "fatal-theme-" + backend,
            "component": "theme",
            "fence": 1000,
            "artifact_sha256": sha256(artifact).hexdigest(),
            "artifact_archive": base64.b64encode(artifact).decode(),
            **payload,
        }
        return (
            helper.execute({"protocol_version": 1, "action": action, **args})
            if backend == "ssh"
            else asyncio.run(pipeline.call(action, args))
        )

    baseline = operation("prepare")
    deployed = operation("deploy", expected_release=baseline["expected_release"])
    try:
        assert call(client, "discover").status_code >= 500
    finally:
        restored = operation("restore", expected_release=deployed["release_version"])
    assert restored["state"] == "restored"
    assert call(client, "discover").status_code == 200
