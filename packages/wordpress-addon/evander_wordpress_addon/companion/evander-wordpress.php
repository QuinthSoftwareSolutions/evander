<?php
/**
 * Plugin Name: Evander WordPress Companion
 * Description: Versioned resources, prepared edits, scoped browser sessions and conditional undo.
 * Version: 1.0.1
 * Requires PHP: 8.1
 * License: GPL-2.0-or-later
 */
if (!defined('ABSPATH')) { exit; }
require_once __DIR__ . '/resources.php';

final class Evander_WordPress {
    const PROTOCOL = 1;
    private static $internal = false;
    private static $browser = null;
    private static $browser_transaction = false;
    private static $transaction = false;
    private static $cache_dirty = false;
    private static $application_authenticated = false;

    public static function application_login() { self::$application_authenticated = true; }

    public static function table() { global $wpdb; return $wpdb->prefix . 'evander_changes'; }
    public static function install() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta('CREATE TABLE ' . self::table() . ' (
            id varchar(160) NOT NULL, owner bigint unsigned NOT NULL,
            payload longtext NOT NULL, PRIMARY KEY (id)
        ) ' . $wpdb->get_charset_collate() . ';');
        if (false === get_option('evander_fence', false)) { add_option('evander_fence', 0, '', false); }
    }
    public static function canonical($value) {
        if (is_array($value)) {
            if (!array_is_list($value)) { ksort($value, SORT_STRING); }
            foreach ($value as $key => $item) { $value[$key] = self::canonical($item); }
        } elseif (is_object($value)) { $value = self::canonical((array)$value); }
        return $value;
    }
    public static function hash($value) {
        return hash('sha256', wp_json_encode(self::canonical($value), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    }
    private static function record($id, $lock = false) {
        global $wpdb;
        $row = $wpdb->get_row($wpdb->prepare('SELECT * FROM ' . self::table() . ' WHERE id=%s' . ($lock ? ' FOR UPDATE' : ''), $id), ARRAY_A);
        if (!$row) { throw new RuntimeException('Unknown change', 409); }
        $data = json_decode($row['payload'], true, 512, JSON_THROW_ON_ERROR);
        $data['owner'] = (int)$row['owner'];
        return $data;
    }
    private static function save($id, $data) {
        global $wpdb;
        $owner = $data['owner']; unset($data['owner']);
        if (false === $wpdb->replace(self::table(), ['id' => $id, 'owner' => $owner, 'payload' => wp_json_encode($data)])) {
            throw new RuntimeException('Could not persist recovery journal');
        }
    }
    private static function begin($cache_dirty = true) {
        if (self::$transaction) { throw new RuntimeException('A request transaction is already active',409); }
        global $wpdb; self::$internal = true;
        if (false === $wpdb->query('START TRANSACTION')) { self::$internal = false; throw new RuntimeException('Could not start transaction'); }
        self::$transaction = true; self::$cache_dirty = $cache_dirty;
    }
    private static function commit() {
        global $wpdb;
        if (false === $wpdb->query('COMMIT')) { throw new RuntimeException('Could not commit transaction'); }
        self::$transaction = false; self::$browser_transaction = false; self::$cache_dirty = false; self::$internal = false;
    }
    private static function rollback() {
        self::$browser_transaction = false;
        if (!self::$transaction) { return; }
        global $wpdb; self::$internal = true; $wpdb->query('ROLLBACK');
        self::$transaction = false; self::$internal = false;
        $dirty = self::$cache_dirty; self::$cache_dirty = false;
        if ($dirty) { wp_cache_flush(); }
    }
    private static function fence($token) {
        global $wpdb;
        $current = $wpdb->get_var($wpdb->prepare("SELECT option_value FROM $wpdb->options WHERE option_name=%s FOR UPDATE", 'evander_fence'));
        if (!is_int($token) || $token < (int)$current) { throw new RuntimeException('Stale mutation owner', 409); }
        $wpdb->update($wpdb->options, ['option_value' => (string)$token], ['option_name' => 'evander_fence']);
        wp_cache_delete('evander_fence', 'options');
    }
    public static function permission() {
        // The host uses an application password over HTTPS. A browser cookie can never invoke host operations.
        $user = get_current_user_id();
        return is_ssl() && self::$application_authenticated && $user && !self::$browser && current_user_can('edit_theme_options');
    }
    public static function routes() {
        foreach (['discover', 'inspect', 'prepare', 'prepare-extend', 'arm-extend', 'arm', 'apply', 'status', 'restore', 'close', 'probe', 'browser/ticket'] as $name) {
            register_rest_route('evander/v1', '/' . $name, ['methods' => in_array($name, ['discover','inspect','status']) ? ['GET','POST'] : 'POST',
                'permission_callback' => [self::class, 'permission'],
                'callback' => function($request) use ($name) { return self::handle($name, $request); }]);
        }
        register_rest_route('evander/v1', '/browser/redeem', ['methods' => 'POST', 'permission_callback' => '__return_true',
            'callback' => function($request) { return self::handle('browser/redeem', $request); }]);
    }
    private static function adapters() {
        $adapters = apply_filters('evander_resource_adapters', [new Evander_Core_Resources()]);
        foreach ($adapters as $adapter) {
            if (!($adapter instanceof Evander_Resource_Adapter)) { throw new RuntimeException('Invalid resource adapter'); }
        }
        return $adapters;
    }
    private static function adapter($key) {
        foreach (self::adapters() as $adapter) { if ($adapter->supports($key)) { return $adapter; } }
        throw new RuntimeException('Resource has no recovery adapter', 400);
    }
    private static function validate_edit($adapter, $key, $value) {
        $adapter->validate($key, $value);
        if ($adapter instanceof Evander_Core_Resources) { $adapter->validate_edit($key, $value); }
    }
    private static function resources($data) {
        foreach ($data['resources'] as $key => $row) {
            $adapter = self::adapter($row['resolved_key'] ?? $key);
            $adapter->lock($row['resolved_key'] ?? $key);
        }
    }
    private static function owned($data) {
        if ($data['owner'] !== get_current_user_id()) { throw new RuntimeException('Change owner mismatch', 403); }
    }
    private static function receipt($data) {
        $resources = [];
        foreach ($data['resources'] ?? [] as $key => $row) {
            $resources[$key] = ['version' => $row['result_version'] ?? $row['before_version'],
                               'resolved_key' => $row['resolved_key'] ?? $key];
        }
        return ['protocol_version' => 1, 'state' => $data['state'], 'resources' => $resources,
                'browser_expires_at' => $data['browser_expires_at'] ?? null];
    }
    public static function handle($name, $request) {
        // Redemption must not replace or roll back the scoped request's transaction.
        if ($name === 'browser/redeem' && self::$browser) {
            return new WP_Error('evander_operation_failed', 'Browser sessions cannot redeem tickets.', ['status'=>403]);
        }
        try {
            if (strlen($request->get_body()) > 16 * 1024 * 1024) { throw new RuntimeException('Request too large', 413); }
            $args = $request->get_json_params() ?: $request->get_params();
            if ($name === 'discover') {
                global $wpdb;
                $tables = [$wpdb->posts, $wpdb->postmeta, $wpdb->options, $wpdb->terms, $wpdb->termmeta, $wpdb->term_taxonomy, $wpdb->term_relationships, $wpdb->usermeta, self::table()];
                $transactional = true;
                foreach ($tables as $table) {
                    $engine = $wpdb->get_var($wpdb->prepare('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', $table));
                    if (strtoupper((string)$engine) !== 'INNODB') { $transactional = false; }
                }
                $coverage = [];
                foreach (self::adapters() as $adapter) { $coverage[] = $adapter->discover(); }
                return ['protocol_version' => 1, 'transactional' => $transactional, 'adapters' => $coverage,
                    'post_types' => array_keys(get_post_types(['show_ui' => true])),
                    'theme' => get_stylesheet(), 'browser_ready' => self::browser_compatible(),
                    'languages' => apply_filters('wpml_active_languages', null, ['skip_missing' => 0]) ?: [],
                    'translation_policy' => 'Only explicitly addressed records are changed; language relationships are preserved.'];
            }
            if ($name === 'inspect') {
                $key = (string)($args['key'] ?? ''); $value = self::adapter($key)->read($key);
                return ['protocol_version' => 1, 'key' => $key, 'value' => $value, 'version' => self::hash($value)];
            }
            if ($name === 'browser/redeem') { return self::redeem($args); }
            $id = (string)($args['change_id'] ?? '');
            if (!preg_match('/^wp_[a-f0-9]{32}$/D', $id)) { throw new RuntimeException('Invalid change ID', 400); }
            self::begin();
            if ($name === 'prepare') {
                if (empty($args['resources']) || count($args['resources']) > 200) { throw new RuntimeException('Resources required', 400); }
                global $wpdb;
                $existing = $wpdb->get_var($wpdb->prepare('SELECT id FROM ' . self::table() . ' WHERE id=%s FOR UPDATE', $id));
                if ($existing) {
                    $data = self::record($id, true); self::owned($data);
                    if ($data['request_hash'] !== self::hash($args['resources'])) { throw new RuntimeException('Change ID already used', 409); }
                } else {
                    $data = ['owner' => get_current_user_id(), 'state' => 'prepared', 'resources' => [],
                        'request_hash' => self::hash($args['resources']), 'expires_at' => min(time() + 86400, (int)$args['expires_at'])];
                    foreach ($args['resources'] as $row) {
                        $key = (string)$row['key'];
                        if (isset($data['resources'][$key])) { throw new RuntimeException('Duplicate resource', 400); }
                        $adapter = self::adapter($key); $adapter->lock($key); $before = $adapter->read($key);
                        self::validate_edit($adapter, $key, $row['value']);
                        if (self::hash($before) !== $row['expected_version']) { throw new RuntimeException('Resource changed', 409); }
                        $data['resources'][$key] = ['before' => $before, 'before_version' => self::hash($before), 'value' => $row['value']];
                    }
                    self::save($id, $data);
                }
                self::commit();
                return ['protocol_version' => 1, 'state' => 'prepared', 'snapshot' => $data['resources']];
            }
            if ($name === 'probe') {
                // A transaction rolls back an actual prepared option round trip. No public data changes.
                $key = 'option:evander_restore_probe'; $adapter = self::adapter($key);
                $adapter->lock($key); $before = $adapter->read($key);
                $adapter->write($key, ['value'=>['probe' => wp_generate_uuid4()],'autoload'=>'off']); $adapter->write($key, $before);
                $ok = self::hash($adapter->read($key)) === self::hash($before);
                self::rollback();
                return ['protocol_version' => 1, 'restore_verified' => $ok];
            }
            $data = self::record($id, true); self::owned($data);
            if ($name === 'prepare-extend') {
                if (!in_array($data['state'], ['prepared','armed','browser_open'], true) || $data['expires_at'] <= time()) { throw new RuntimeException('Change cannot be extended',409); }
                $extension = (string)($args['idempotency_key'] ?? '');
                if (!preg_match('/^[a-zA-Z0-9._:-]{8,128}$/D',$extension) || empty($args['resources']) || count($args['resources'])+count($data['resources'])>200) { throw new RuntimeException('Invalid extension',400); }
                $hash = self::hash($args['resources']);
                if (isset($data['extensions'][$extension])) {
                    if ($data['extensions'][$extension]['hash'] !== $hash) { throw new RuntimeException('Extension key already used',409); }
                    $snapshot = $data['extensions'][$extension]['snapshot'];
                } else {
                    $snapshot = [];
                    foreach ($args['resources'] as $row) {
                        $key = $row['key'];
                        if (isset($data['resources'][$key]) || isset($snapshot[$key])) { throw new RuntimeException('Resource is already covered',409); }
                        $adapter = self::adapter($key); $adapter->lock($key); $before = $adapter->read($key); self::validate_edit($adapter,$key,$row['value']);
                        if (self::hash($before) !== $row['expected_version']) { throw new RuntimeException('Extension resource changed',409); }
                        $snapshot[$key] = ['before'=>$before,'before_version'=>self::hash($before),'value'=>$row['value']];
                    }
                    $data['extensions'][$extension] = ['hash'=>$hash,'snapshot'=>$snapshot,'armed'=>false]; self::save($id,$data);
                }
                self::commit(); return ['protocol_version'=>1,'snapshot'=>$snapshot];
            }
            if ($name === 'status') { self::commit(); return self::receipt($data); }
            if ($name === 'arm-extend') {
                self::fence($args['fence']);
                if (!in_array($data['state'],['prepared','armed','browser_open'],true) || $data['expires_at']<=time()) { throw new RuntimeException('Change cannot be extended',409); }
                $extension = $args['idempotency_key'];
                if (empty($data['extensions'][$extension]) || empty($args['snapshot_digest'])) { throw new RuntimeException('Extension has no durable snapshot',409); }
                if (!$data['extensions'][$extension]['armed']) {
                    foreach ($data['extensions'][$extension]['snapshot'] as $key=>$row) {
                        $adapter = self::adapter($key); $adapter->lock($key);
                        if (self::hash($adapter->read($key)) !== $row['before_version']) { throw new RuntimeException('Extension resource changed',409); }
                        $data['resources'][$key] = $row;
                    }
                    $data['extensions'][$extension]['armed'] = true;
                }
                $data['snapshot_digest'] = $args['snapshot_digest'];
            } elseif ($name === 'arm') {
                self::fence($args['fence']);
                if (empty($args['snapshot_digest'])) { throw new RuntimeException('Host snapshot is not durable', 400); }
                if (!in_array($data['state'], ['prepared','armed','browser_open'])) { throw new RuntimeException('Change cannot be armed', 409); }
                $data['snapshot_digest'] = $args['snapshot_digest']; $data['state'] = 'armed';
            } elseif ($name === 'apply') {
                self::fence($args['fence']); self::resources($data);
                if ($data['state'] !== 'applied') {
                    if ($data['state'] !== 'armed' || $data['expires_at'] <= time()) { throw new RuntimeException('Change is not armed or has expired', 409); }
                    foreach ($data['resources'] as $key => &$row) {
                        $adapter = self::adapter($key);
                        if (self::hash($adapter->read($key)) !== $row['before_version']) { throw new RuntimeException('Resource changed', 409); }
                        self::validate_edit($adapter, $key, $row['value']);
                        $resolved = $adapter->write($key, $row['value']);
                        $row['resolved_key'] = $resolved ?: $key;
                        $row['result_version'] = self::hash($adapter->read($row['resolved_key']));
                    } unset($row);
                    $data['state'] = 'applied';
                }
            } elseif ($name === 'restore') {
                self::fence($args['fence']); self::resources($data);
                if ($data['state'] !== 'restored') {
                    if (empty($args['snapshot'])) { throw new RuntimeException('External recovery manifest required', 400); }
                    foreach ($data['resources'] as $key => &$row) {
                        $address = $row['resolved_key'] ?? $key; $adapter = self::adapter($address);
                        $current = self::hash($adapter->read($address));
                        if ($current === $row['before_version']) { continue; }
                        if (empty($row['result_version']) || $current !== $row['result_version']) { throw new RuntimeException('Human edit conflicts with restore', 409); }
                        if (!isset($args['snapshot'][$key]) || self::hash($args['snapshot'][$key]['before']) !== $row['before_version']) { throw new RuntimeException('Snapshot mismatch', 409); }
                        $adapter->write($address, $args['snapshot'][$key]['before']);
                        if (self::hash($adapter->read($address)) !== $row['before_version']) { throw new RuntimeException('Restore verification failed'); }
                    } unset($row);
                    $data['state'] = 'restored';
                }
                self::revoke($data);
            } elseif ($name === 'close') {
                self::resources($data); self::revoke($data);
                if ($data['state'] === 'browser_open') { $data['state'] = 'applied'; }
            } elseif ($name === 'browser/ticket') {
                if (!self::browser_compatible() || !in_array($data['state'],['armed','browser_open'],true) || $data['expires_at'] <= time()) { throw new RuntimeException('Browser recovery coverage is unavailable', 409); }
                self::fence($args['fence']);
                self::revoke($data);
                $ticket = bin2hex(random_bytes(32));
                $data['ticket_generation'] = hash('sha256',$ticket);
                self::save('ticket:' . hash('sha256', $ticket), ['owner' => $data['owner'], 'change_id' => $id,
                    'generation' => $data['ticket_generation'],
                    'expires_at' => time() + 60, 'browser_expiry' => min(time() + 900, $data['expires_at'], (int)$args['expires_at'])]);
                $data['state'] = 'browser_open'; self::save($id, $data); self::commit();
                return ['protocol_version' => 1, 'ticket' => $ticket, 'expires_at' => time() + 60];
            }
            self::save($id, $data); self::commit();
            return self::receipt($data);
        } catch (Throwable $error) {
            self::rollback();
            $status = in_array($error->getCode(), [400,403,409,413]) ? $error->getCode() : 503;
            // Deliberately never echo arguments, SQL, cookies, credentials or PHP exception chains.
            return new WP_Error('evander_operation_failed', 'WordPress operation could not complete.', ['status' => $status]);
        }
    }
    private static function browser_compatible() {
        // Builders that write files/custom tables must declare adapters and request coverage.
        $approved = apply_filters('evander_browser_compatible_plugins', ['evander-wordpress/evander-wordpress.php']);
        $active = get_option('active_plugins', []);
        if (is_multisite()) {
            $network = array_keys(get_site_option('active_sitewide_plugins', []));
            // Cookies and users span blogs. Every blog must enforce the session marker.
            if (!in_array(plugin_basename(__FILE__), $network, true)) { return false; }
            $active = array_unique(array_merge($active, $network));
        }
        foreach ($active as $plugin) { if (!in_array($plugin, $approved, true)) { return false; } }
        return true;
    }
    private static function redeem($args) {
        if (!is_ssl() || !preg_match('/^[a-f0-9]{64}$/D', $args['ticket'] ?? '')) { throw new RuntimeException('Ticket denied', 403); }
        self::begin(false);
        $key = 'ticket:' . hash('sha256', $args['ticket']); $ticket = self::record($key, true);
        if ($ticket['expires_at'] <= time()) { throw new RuntimeException('Ticket expired', 403); }
        global $wpdb; $wpdb->delete(self::table(), ['id' => $key]);
        $data = self::record($ticket['change_id'], true);
        if ($data['state'] !== 'browser_open' || $data['expires_at'] <= time() || ($ticket['generation']??'') !== ($data['ticket_generation']??null)) { throw new RuntimeException('Change expired', 403); }
        $expires = $ticket['browser_expiry']; self::$cache_dirty = true;
        $marker = function($session) use ($ticket) {
            $session['evander_change_id'] = $ticket['change_id'];
            $session['evander_blog_id'] = get_current_blog_id();
            return $session;
        };
        add_filter('attach_session_information', $marker, PHP_INT_MAX);
        try { $token = WP_Session_Tokens::get_instance($ticket['owner'])->create($expires); }
        finally { remove_filter('attach_session_information', $marker, PHP_INT_MAX); }
        $data['browser_token'] = $token; $data['browser_expires_at'] = $expires;
        self::save($ticket['change_id'], $data);
        self::save('browser:' . hash('sha256', $token), ['owner' => $ticket['owner'], 'change_id' => $ticket['change_id'], 'expires_at' => $expires]);
        $host = wp_parse_url(home_url(), PHP_URL_HOST);
        $cookies = [];
        foreach ([[LOGGED_IN_COOKIE,'logged_in','/'],[SECURE_AUTH_COOKIE,'secure_auth',ADMIN_COOKIE_PATH],
                  [SECURE_AUTH_COOKIE,'secure_auth',PLUGINS_COOKIE_PATH]] as $spec) {
            $cookies[] = ['name' => $spec[0], 'value' => wp_generate_auth_cookie($ticket['owner'], $expires, $spec[1], $token),
                'domain' => $host, 'path' => $spec[2], 'expires' => $expires, 'secure' => true, 'httpOnly' => true, 'sameSite' => 'Lax'];
        }
        self::commit();
        $url = wp_parse_url(home_url());
        $origin = $url['scheme'] . '://' . $url['host'] . (isset($url['port']) ? ':' . $url['port'] : '');
        return ['protocol_version' => 1, 'origin' => $origin, 'expiresAt' => $expires,
                'storageState' => ['cookies' => $cookies, 'origins' => []], 'sessionStorage' => new stdClass()];
    }
    private static function revoke(&$data) {
        if (!empty($data['browser_token'])) {
            WP_Session_Tokens::get_instance($data['owner'])->destroy($data['browser_token']);
            global $wpdb; $wpdb->delete(self::table(), ['id' => 'browser:' . hash('sha256', $data['browser_token'])]);
            unset($data['browser_token']);
        }
        $data['browser_expires_at'] = null;
    }
    public static function browser_cookie($cookie, $user) {
        $session = WP_Session_Tokens::get_instance($user->ID)->get($cookie['token']);
        if (empty($session['evander_change_id'])) { return; }
        try {
            if (!is_ssl() || (int)($session['evander_blog_id'] ?? 0) !== get_current_blog_id()) { throw new RuntimeException(); }
            $binding = self::record('browser:' . hash('sha256', $cookie['token']));
            $data = self::record($binding['change_id']);
            if ($binding['owner'] !== (int)$user->ID || $binding['change_id'] !== $session['evander_change_id'] ||
                $binding['expires_at'] <= time() || $data['state'] !== 'browser_open' ||
                ($data['browser_token'] ?? '') !== $cookie['token']) { throw new RuntimeException(); }
            // Admin pages authenticate again after init. Retain the locked manifest
            // for the same session, and never switch change ownership mid-request.
            if (self::$browser && ($binding['owner'] !== self::$browser['owner'] ||
                $binding['change_id'] !== self::$browser['change_id'] ||
                $binding['expires_at'] !== self::$browser['expires_at'])) { throw new RuntimeException(); }
            if (!self::$browser) { self::$browser = $binding; }
        } catch (Throwable $ignored) { self::rollback(); wp_die('Browser change session denied', '', ['response'=>403]); }
    }
    public static function browser_init() {
        // An admin cookie alone must enforce the same scope as a logged-in cookie.
        $cookie = wp_parse_auth_cookie('', is_ssl() ? 'secure_auth' : 'auth') ?: wp_parse_auth_cookie('', 'logged_in');
        if (!$cookie) { return; }
        $owner = (int)wp_validate_auth_cookie('', $cookie['scheme']);
        if (!self::$browser) { return; }
        $binding = self::$browser;
        if ($owner !== $binding['owner']) { wp_die('Browser cookie denied', '', ['response'=>403]); }
        if ($binding['expires_at'] <= time()) { wp_die('Browser change session expired', '', ['response' => 403]); }
        self::begin(false);
        $data = self::record($binding['change_id'], true);
        if ($data['state'] !== 'browser_open' || $data['browser_token'] !== $cookie['token']) { self::rollback(); wp_die('Browser change session closed', '', ['response'=>403]); }
        self::resources($data);
        // Recheck all resource versions under row locks at the beginning of each request.
        foreach ($data['resources'] as $key => $row) {
            if (self::hash(self::adapter($key)->read($key)) !== ($row['result_version'] ?? $row['before_version'])) {
                self::rollback(); wp_die('Concurrent edit requires a new prepared change', '', ['response'=>409]);
            }
        }
        self::$browser['data'] = $data; self::$browser_transaction = true; self::$internal = false;
        // Scoped design requests do not run or reschedule unrelated core maintenance.
        remove_action('init', 'wp_cron'); remove_action('wp_loaded', '_wp_cron');
        foreach (['_maybe_update_core','_maybe_update_plugins','_maybe_update_themes'] as $callback) { remove_action('admin_init', $callback); }
        add_filter('pre_update_option_cron', function($new, $old) { return $old; }, PHP_INT_MAX, 2);
        add_filter('query', [self::class, 'guard_query'], PHP_INT_MAX);
        add_action('shutdown', [self::class, 'browser_commit'], PHP_INT_MAX);
        add_filter('wp_revisions_to_keep', '__return_zero', PHP_INT_MAX);
        // The block editor lazily creates an empty theme-global styles post even
        // when editing a page. Defer that unrelated initialization; existing,
        // explicitly covered styles records still use the normal write guard.
        add_filter('wp_insert_post_empty_content', function($empty, $post) {
            return $empty || (empty($post['ID']) && ($post['post_type'] ?? '') === 'wp_global_styles');
        }, PHP_INT_MAX, 2);
        // Resolve an uncached custom-CSS lookup without persisting a theme-mod
        // cache entry. Keep both existing CSS and the captured options unchanged.
        add_filter('theme_mod_custom_css_post_id', function($id) {
            if ($id === -1 || ($id > 0 && get_post($id))) { return $id; }
            $posts = get_posts(['post_type'=>'custom_css', 'post_status'=>get_post_stati(),
                'name'=>sanitize_title(get_stylesheet()), 'posts_per_page'=>1, 'fields'=>'ids',
                'no_found_rows'=>true, 'update_post_meta_cache'=>false, 'update_post_term_cache'=>false]);
            return $posts ? (int)$posts[0] : -1;
        }, PHP_INT_MAX);
    }
    private static function browser_allowed_caps() {
        return ['exist','read','edit_posts','edit_pages','edit_published_posts','edit_published_pages','publish_posts','publish_pages',
                'edit_others_posts','edit_others_pages','edit_theme_options'];
    }
    public static function browser_meta_caps($caps) {
        // Multisite super-admins bypass user_has_cap unless map_meta_cap denies
        // the operation with do_not_allow. Temporary cookies must remain scoped.
        if (self::$browser && array_diff($caps, self::browser_allowed_caps())) { return ['do_not_allow']; }
        return $caps;
    }
    public static function browser_caps($caps) {
        if (!self::$browser) { return $caps; }
        $allowed = self::browser_allowed_caps();
        foreach ($caps as $key => $value) { if (!in_array($key, $allowed, true)) { $caps[$key] = false; } }
        return $caps;
    }
    public static function guard_query($sql) {
        if (self::$internal || !preg_match('/^\s*(UPDATE|INSERT|REPLACE|DELETE|ALTER|CREATE|DROP|TRUNCATE|COMMIT|ROLLBACK|START|SET)\b/i', $sql)) { return $sql; }
        foreach (self::$browser['data']['resources'] as $key => $row) {
            if (self::adapter($key)->allows_query($key, $sql)) { self::$cache_dirty = true; return $sql; }
        }
        self::rollback(); self::$browser_transaction = false;
        wp_die('This write is outside the prepared recovery manifest', '', ['response'=>409]);
    }
    public static function browser_commit() {
        if (!self::$browser_transaction || !self::$transaction) { return; }
        self::$internal = true;
        $error = error_get_last();
        if ($error && in_array($error['type'], [E_ERROR,E_PARSE,E_CORE_ERROR,E_COMPILE_ERROR])) { self::rollback(); return; }
        $data = self::$browser['data'];
        foreach ($data['resources'] as $key => &$row) { $row['result_version'] = self::hash(self::adapter($key)->read($key)); } unset($row);
        self::save(self::$browser['change_id'], $data); self::commit();
    }
}
register_activation_hook(__FILE__, [Evander_WordPress::class, 'install']);
add_action('rest_api_init', [Evander_WordPress::class, 'routes']);
add_action('init', [Evander_WordPress::class, 'browser_init'], -100);
add_filter('user_has_cap', [Evander_WordPress::class, 'browser_caps'], PHP_INT_MAX);
add_filter('map_meta_cap', [Evander_WordPress::class, 'browser_meta_caps'], PHP_INT_MAX);
add_action('application_password_did_authenticate', [Evander_WordPress::class, 'application_login']);
add_action('auth_cookie_valid', [Evander_WordPress::class, 'browser_cookie'], PHP_INT_MAX, 2);
