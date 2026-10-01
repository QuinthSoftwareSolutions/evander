"""Opt-in real WordPress/PHP/MariaDB tests; resources are removed even on failure.

EVANDER_RUN_WORDPRESS_INTEGRATION=1 pytest -q packages/wordpress-addon/tests/test_wordpress_integration.py
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
    for path in (ROOT / "tests/fixtures/theme").iterdir():
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
            str(ROOT / "evander_wordpress_addon/companion") + ":/companion:ro",
            "-v",
            str(ROOT / "tests/fixtures") + ":/fixtures:ro",
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


@pytest.mark.parametrize("existing_styles", [False, True])
def test_real_block_editor_load_save_and_undo_keep_scope(wordpress, existing_styles):
    client, data, wp = wordpress
    theme = docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; echo get_stylesheet();")
    # A fresh block theme triggers both lazy global-styles creation and an
    # uncached custom-CSS lookup in the real wp-admin editor request.
    setup = """
require '/var/www/html/wp-load.php';
wp_set_current_user(get_user_by('login','designer')->ID);
switch_theme('twentytwentyfive');
$css=wp_insert_post(['post_type'=>'custom_css','post_status'=>'publish',
    'post_name'=>'twentytwentyfive','post_content'=>'.fixture-css { color: rgb(12, 34, 56); }']);
remove_theme_mod('custom_css_post_id');
"""
    if existing_styles:
        setup += "WP_Theme_JSON_Resolver::get_user_global_styles_post_id();"
    setup += "echo wp_json_encode(['css'=>$css,'styles'=>get_posts(['post_type'=>'wp_global_styles','fields'=>'ids','posts_per_page'=>-1])]);"
    initialized = json.loads(docker("exec", wp, "php", "-r", setup))
    assert bool(initialized["styles"]) == existing_styles
    key = "post:" + str(data["post"])
    keys = [key, "meta:" + str(data["post"]) + ":_edit_lock", "meta:" + str(data["post"]) + ":_edit_last"]
    originals = {address: inspect(client, address) for address in keys}
    option = inspect(client, "option:theme_mods_twentytwentyfive")
    css = inspect(client, "post:" + str(initialized["css"]))
    styles = {"post:" + str(id): inspect(client, "post:" + str(id)) for id in initialized["styles"]}
    other = inspect(client, "post:" + str(data["other"]))
    try:
        id, snapshot = prepare(client, {address: {} if address == key else originals[address]["value"] for address in keys})
        ticket = call(client, "browser/ticket", {"change_id": id, "fence": 100,
            "expires_at": int(time.time() + 900)}).json()["ticket"]
        cookies = call(client, "browser/redeem", {"ticket": ticket}).json()["storageState"]["cookies"]
        cookie = "; ".join(row["name"] + "=" + row["value"] for row in cookies
                           if row["path"] == "/" or row["path"].endswith("/wp-admin"))
        with ContainerClient(wp, headers={"Cookie": cookie}) as browser:
            editor = browser.request("GET", "/wp-admin/post.php?post=" + str(data["post"]) + "&action=edit")
            assert editor.status_code == 200, editor.text[-1000:]
            assert "rgb(12, 34, 56)" in editor.text, "Existing custom CSS must still reach the editor"
            assert inspect(client, keys[1])["value"], "The covered edit lock must be saved, not suppressed"
            nonce = browser.request("GET", "/wp-admin/admin-ajax.php?action=rest-nonce").text
        with ContainerClient(wp, headers={"Cookie": cookie, "X-WP-Nonce": nonce}) as browser:
            saved = browser.post("/index.php?rest_route=/wp/v2/pages/" + str(data["post"]),
                json={"title": "Block editor saved", "content": "<!-- wp:paragraph --><p>Covered content.</p><!-- /wp:paragraph -->"})
            assert saved.status_code == 200, saved.text
            assert inspect(client, key)["value"]["post_title"] == "Block editor saved"
            assert browser.post("/index.php?rest_route=/wp/v2/pages/" + str(data["other"]),
                json={"title": "Uncovered editor write"}).status_code == 409
            assert browser.request("GET", "/wp-admin/options.php").status_code == 403
        assert inspect(client, "post:" + str(data["other"])) == other
        assert inspect(client, "option:theme_mods_twentytwentyfive") == option
        assert inspect(client, css["key"]) == css
        assert all(inspect(client, address) == before for address, before in styles.items())
        count = docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; global $wpdb; echo $wpdb->get_var(\"SELECT COUNT(*) FROM $wpdb->posts WHERE post_type='wp_global_styles'\");")
        assert int(count) == len(styles), "Editor loading must not create an untracked styles record"
        assert call(client, "close", {"change_id": id}).status_code == 200
        restored = call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot})
        assert restored.status_code == 200, restored.text
        assert all(inspect(client, address) == before for address, before in originals.items())
        # Normal operator requests retain WordPress's initialization behavior.
        normal = docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; wp_set_current_user(get_user_by('login','designer')->ID); echo WP_Theme_JSON_Resolver::get_user_global_styles_post_id();")
        assert int(normal) > 0
    finally:
        docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; foreach (get_posts(['post_type'=>'wp_global_styles','post_status'=>'any','posts_per_page'=>-1]) as $post) { wp_delete_post($post->ID,true); } wp_delete_post(" + str(initialized["css"]) + ",true); switch_theme('" + theme + "');")


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


def test_anonymous_invalid_redemption_does_not_flush_cache_but_real_rollback_does(wordpress):
    client, data, wp = wordpress
    result = json.loads(docker("exec", wp, "php", "-r", """
require '/var/www/html/wp-load.php';
class Evander_Fixture_Flush_Counter extends WP_Object_Cache {
    public $calls=0;
    public function flush() { ++$this->calls; return parent::flush(); }
}
global $wp_object_cache; $wp_object_cache=new Evander_Fixture_Flush_Counter();
wp_set_current_user(0); $results=[];
foreach (['','malformed',str_repeat('0',64)] as $ticket) {
    $request=new WP_REST_Request('POST','/evander/v1/browser/redeem');
    $request->set_header('content-type','application/json'); $request->set_body(wp_json_encode(['ticket'=>$ticket]));
    $response=rest_do_request($request);
    $results[]=['status'=>$response->get_status(),'flushes'=>$wp_object_cache->calls];
}
wp_set_current_user(get_user_by('login','designer')->ID); Evander_WordPress::application_login();
$request=new WP_REST_Request('POST','/evander/v1/probe');
$request->set_header('content-type','application/json');
$request->set_body(wp_json_encode(['change_id'=>'wp_'.str_repeat('a',32)]));
$response=rest_do_request($request);
echo wp_json_encode(['invalid'=>$results,'probe_status'=>$response->get_status(),'flushes'=>$wp_object_cache->calls]);
"""))
    assert [row["status"] for row in result["invalid"]] == [403, 403, 409]
    assert all(row["flushes"] == 0 for row in result["invalid"])
    assert result["probe_status"] == 200 and result["flushes"] == 1


@pytest.mark.parametrize("address", ["existing", "explicit", "symbolic"])
@pytest.mark.parametrize("html", ['<script>window.fixtureUnsafe=true</script>', '<img src="x" onerror="window.fixtureUnsafe=true">'])
def test_restricted_cms_account_cannot_prepare_executable_post_html(wordpress, address, html):
    client, data, wp = wordpress
    key = {"existing": "post:" + str(data["post"]), "explicit": "post:987654",
           "symbolic": "post:new-restricted"}[address]
    before = inspect(client, key)
    with ContainerClient(wp, auth=("restricted-designer", data["restricted_password"])) as restricted:
        response = call(restricted, "prepare", {"change_id": "wp_" + uuid4().hex,
            "expires_at": int(time.time() + 3600), "resources": [{"key": key,
                "expected_version": before["version"], "value": {"post_content": html}}]})
        assert response.status_code == 403, response.text
    assert inspect(client, key) == before


def test_restricted_cms_edits_and_undo_preserve_exact_captured_html(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["captured_html"])
    before = inspect(client, key)
    assert "<script>" in before["value"]["post_content"]
    with ContainerClient(wp, auth=("restricted-designer", data["restricted_password"])) as restricted:
        safe = '<p><strong>Safe content</strong> & text <a href="https://example.test/?a=1&b=2">Link</a></p>'
        id, snapshot = prepare(restricted, {key: {"post_title": "Research & Development", "post_content": safe}})
        assert call(restricted, "apply", {"change_id": id, "fence": 100}).status_code == 200
        current = inspect(client, key)["value"]
        assert current["post_content"] == safe and current["post_title"] == "Research & Development"
        assert call(restricted, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code == 200
    assert inspect(client, key) == before


def test_cms_rechecks_html_capability_on_apply_and_extension(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    before = inspect(client, key)
    docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; (new WP_User(" + str(data["restricted_user"]) + "))->add_cap('unfiltered_html');")
    with ContainerClient(wp, auth=("restricted-designer", data["restricted_password"])) as restricted:
        try:
            id, snapshot = prepare(restricted, {key: {"post_content": "<script>window.authorizedFixture=true</script>"}})
        finally:
            docker("exec", wp, "php", "-r", "require '/var/www/html/wp-load.php'; (new WP_User(" + str(data["restricted_user"]) + "))->remove_cap('unfiltered_html');")
        assert call(restricted, "apply", {"change_id": id, "fence": 100}).status_code == 403
        other = "post:" + str(data["other"])
        extended = call(restricted, "prepare-extend", {"change_id": id,
            "idempotency_key": "unsafe-extension", "resources": [{"key": other,
                "expected_version": inspect(client, other)["version"],
                "value": {"post_excerpt": '<img src="x" onerror="alert(1)">'}}]})
        assert extended.status_code == 403
    assert inspect(client, key) == before


def test_authorized_unfiltered_cms_html_remains_supported(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    before = inspect(client, key)
    html = "<script>window.authorizedFixture=true</script><p>Authorized content</p>"
    id, snapshot = prepare(client, {key: {"post_content": html}})
    assert call(client, "apply", {"change_id": id, "fence": 100}).status_code == 200
    assert inspect(client, key)["value"]["post_content"] == html
    assert call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code == 200
    assert inspect(client, key) == before


def test_scoped_redemption_preserves_locks_and_does_not_attribute_human_edit(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    id, snapshot = prepare(client, {key: {"post_title": "Browser proposal"}})
    ticket = call(client, "browser/ticket", {"change_id": id, "fence": 100,
        "expires_at": int(time.time() + 900)}).json()["ticket"]
    cookies = call(client, "browser/redeem", {"ticket": ticket}).json()["storageState"]["cookies"]
    cookie = "; ".join(row["name"] + "=" + row["value"] for row in cookies if row["path"] == "/")
    marker = uuid4().hex
    with ContainerClient(wp, headers={"Cookie": cookie}) as browser, ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(browser.post, "/wp-admin/admin-ajax.php",
            data={"action": "evander_fixture_nested_redeem", "marker": marker})
        for attempt in range(50):
            if docker("exec", wp, "php", "-r", "echo is_file('/tmp/evander-fixture-nested-" + marker + "') ? 'ready' : 'waiting';") == "ready":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Scoped request did not reach the transaction barrier")
        human = pool.submit(docker, "exec", wp, "php", "-r",
            "require '/var/www/html/wp-load.php'; global $wpdb; $wpdb->update($wpdb->posts,['post_title'=>'Concurrent human edit'],['ID'=>" + str(data["post"]) + "]);", timeout=15)
        assert response.result(timeout=10).json()["status"] == 403
        human.result(timeout=15)
    assert call(client, "close", {"change_id": id}).status_code == 200
    assert call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code == 409
    assert inspect(client, key)["value"]["post_title"] == "Concurrent human edit"


def test_scoped_cookie_denies_wordpress_super_admin_capability_shortcut(wordpress):
    client, data, wp = wordpress
    key = "post:" + str(data["post"])
    id, snapshot = prepare(client, {key: {"post_title": "Scoped administrator"}})
    ticket = call(client, "browser/ticket", {"change_id": id, "fence": 100,
        "expires_at": int(time.time() + 900)}).json()["ticket"]
    cookies = call(client, "browser/redeem", {"ticket": ticket}).json()["storageState"]["cookies"]
    # Create the real network tables, then bootstrap this request in multisite
    # mode. Other fixture requests continue using their single-site configuration.
    docker("exec", wp, "php", "-r", """
require '/var/www/html/wp-load.php';
wp_set_current_user(get_user_by('login','designer')->ID);
require_once ABSPATH.'wp-admin/includes/upgrade.php';
foreach ($wpdb->tables('ms_global') as $table=>$name) { $wpdb->$table=$name; }
$permalink=get_option('permalink_structure'); install_network();
$result=populate_network(1,'design.example.test','designer@example.test','Synthetic network');
if (is_wp_error($result)) { throw new RuntimeException('Fixture network setup failed'); }
update_option('permalink_structure',$permalink);
""")
    bootstrap = """
$_SERVER['HTTP_HOST']='design.example.test'; $_SERVER['HTTPS']='on';
$_SERVER['REQUEST_URI']='/';
define('MULTISITE',true); define('SUBDOMAIN_INSTALL',false);
define('DOMAIN_CURRENT_SITE','design.example.test'); define('PATH_CURRENT_SITE','/');
define('SITE_ID_CURRENT_SITE',1); define('BLOG_ID_CURRENT_SITE',1);
$GLOBALS['super_admins']=['designer'];
"""
    # Multisite derives different cookie names from its network URL. Preserve
    # the real signed scope tokens while using the names from its native bootstrap.
    names = json.loads(docker("exec", wp, "php", "-r", bootstrap + """
require '/var/www/html/wp-load.php';
echo wp_json_encode(['logged'=>LOGGED_IN_COOKIE,'secure'=>SECURE_AUTH_COOKIE]);
"""))
    cookie_data = base64.b64encode(json.dumps({
        names["logged"]: next(row["value"] for row in cookies if row["path"] == "/"),
        names["secure"]: next(row["value"] for row in cookies if row["path"] != "/"),
    }).encode()).decode()
    script = "<?php\n" + bootstrap + """
$_COOKIE=json_decode(base64_decode('""" + cookie_data + """'),true);
require '/var/www/html/wp-load.php';
echo wp_json_encode(['super_admin'=>is_super_admin(),'settings'=>current_user_can('manage_options'),
    'design'=>current_user_can('edit_theme_options'),'read'=>current_user_can('read')]);
"""
    result = subprocess.run(["docker", "exec", "-i", wp, "php"], input=script,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-1000:]
    capabilities = json.loads(result.stdout)
    assert capabilities == {"super_admin": True, "settings": False, "design": True, "read": True}
    assert call(client, "close", {"change_id": id}).status_code == 200
    assert call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot}).status_code == 200


def test_created_post_receipt_exposes_assigned_key_for_subsequent_edit(wordpress):
    from evander_wordpress_addon.service import Service

    client, _, _ = wordpress
    symbolic = "post:new-" + uuid4().hex
    creation, snapshot = prepare(client, {symbolic: {"post_title": "Created page"}})
    response = call(client, "apply", {"change_id": creation, "fence": 100})
    assert response.status_code == 200, response.text
    cms_result = response.json()
    cms_result["browser_token"] = "private-browser-state"
    cms_result["resources"][symbolic]["before"] = {"post_content": "private-recovery-data"}
    receipt = Service.receipt(
        {
            "change_id": creation,
            "state": "published",
            "cms_result": cms_result,
            "snapshot": snapshot,
        }
    )
    resource = receipt["resources"][symbolic]
    assert set(resource) == {"resolved_key", "version"}
    key = resource["resolved_key"]
    assert key.startswith("post:") and key.split(":")[1].isdigit()
    created = inspect(client, key)
    assert created["value"]["post_title"] == "Created page"
    assert created["version"] == resource["version"]
    assert "cms_result" not in receipt and "snapshot" not in receipt
    assert "private-browser-state" not in json.dumps(receipt)
    assert "private-recovery-data" not in json.dumps(receipt)
    edit, original = prepare(client, {key: {"post_title": "Edited created page"}})
    assert call(client, "apply", {"change_id": edit, "fence": 100}).status_code == 200
    assert inspect(client, key)["value"]["post_title"] == "Edited created page"
    assert (
        call(client, "restore", {"change_id": edit, "fence": 100, "snapshot": original}).status_code
        == 200
    )
    assert (
        call(
            client, "restore", {"change_id": creation, "fence": 100, "snapshot": snapshot}
        ).status_code
        == 200
    )
    assert inspect(client, key)["value"] is None


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
    for scoped_cookie in [cookie + "; " + admin_cookie, admin_cookie]:
        with ContainerClient(client.container, headers={"Cookie": scoped_cookie}) as browser:
            settings = browser.request("GET", "/wp-admin/options.php")
            assert settings.status_code == 403
            assert "synthetic-persistent-payment-secret" not in settings.text
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


@pytest.mark.parametrize("kind", ["post", "term"])
def test_database_write_failure_rolls_back_all_resources_and_receipt(wordpress, kind):
    client, data, _ = wordpress
    key = "post:" + str(data["post"]) if kind == "post" else "term:" + str(data["menu_term"])
    option = "option:theme_mods_evander-fixture-theme"
    before, option_before = inspect(client, key), inspect(client, option)
    invalid = {"post_name" if kind == "post" else "slug": "x" * 300}
    id, _ = prepare(
        client, {option: {"value": {"custom_color": "#abcdef"}, "autoload": "off"}, key: invalid}
    )
    response = call(client, "apply", {"change_id": id, "fence": 100})
    assert response.status_code == 503, response.text
    assert inspect(client, key) == before
    assert inspect(client, option) == option_before
    assert call(client, "status", {"change_id": id}).json()["state"] == "armed"


@pytest.mark.parametrize(
    "record, status", [("future", "future"), ("attachment", "inherit"), ("autodraft", "auto-draft")]
)
def test_post_restore_preserves_captured_standard_statuses(wordpress, record, status):
    client, data, _ = wordpress
    key = "post:" + str(data[record])
    before = inspect(client, key)
    assert before["value"]["post_status"] == status
    id, snapshot = prepare(client, {key: {"post_title": "Edited metadata"}})
    assert call(client, "apply", {"change_id": id, "fence": 100}).status_code == 200
    assert inspect(client, key)["value"]["post_title"] == "Edited metadata"
    response = call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot})
    assert response.status_code == 200, response.text
    assert inspect(client, key) == before


@pytest.mark.parametrize("kind", ["meta", "termmeta"])
@pytest.mark.parametrize("value", ["scalar", 42, {"caption": "object"}])
def test_metadata_preparation_rejects_nonlist_values_without_deleting_existing_data(
    wordpress, kind, value
):
    client, data, _ = wordpress
    key = (
        "meta:" + str(data["post"]) + ":_builder_data"
        if kind == "meta"
        else "termmeta:" + str(data["menu_term"]) + ":_design_caption"
    )
    before = inspect(client, key)
    assert before["value"], "Exercise existing metadata, not a missing field"
    id = "wp_" + uuid4().hex
    response = call(
        client,
        "prepare",
        {
            "change_id": id,
            "expires_at": int(time.time() + 3600),
            "resources": [{"key": key, "expected_version": before["version"], "value": value}],
        },
    )
    assert response.status_code == 400, response.text
    assert inspect(client, key) == before
    assert call(client, "status", {"change_id": id}).status_code == 409


@pytest.mark.parametrize("kind", ["meta", "termmeta"])
def test_metadata_lists_can_be_published_and_restored(wordpress, kind):
    client, data, _ = wordpress
    key = (
        "meta:" + str(data["post"]) + ":_builder_data"
        if kind == "meta"
        else "termmeta:" + str(data["menu_term"]) + ":_design_caption"
    )
    before = inspect(client, key)
    values = [{"caption": "After"}, "Additional stored value"]
    id, snapshot = prepare(client, {key: values})
    assert call(client, "apply", {"change_id": id, "fence": 100}).status_code == 200
    assert inspect(client, key)["value"] == values
    response = call(client, "restore", {"change_id": id, "fence": 100, "snapshot": snapshot})
    assert response.status_code == 200, response.text
    assert inspect(client, key) == before


def test_zero_affected_rows_and_deleting_missing_metadata_are_successful_noops(wordpress):
    client, data, _ = wordpress
    post = "post:" + str(data["post"])
    missing = "meta:" + str(data["post"]) + ":_missing_design_field"
    before = inspect(client, post)
    id, _ = prepare(client, {post: {"post_title": before["value"]["post_title"]}, missing: None})
    response = call(client, "apply", {"change_id": id, "fence": 100})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "applied"
    assert inspect(client, post) == before
    assert inspect(client, missing)["value"] == []


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
