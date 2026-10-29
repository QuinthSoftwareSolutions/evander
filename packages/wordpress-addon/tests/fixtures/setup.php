<?php
$_SERVER['HTTP_HOST'] = 'design.example.test'; $_SERVER['HTTPS'] = 'on';
define('WP_INSTALLING', true);
require '/var/www/html/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
if (!is_blog_installed()) { wp_install('Synthetic design site', 'designer', 'designer@example.test', true, '', 'fixture-only-password'); }
require_once ABSPATH . 'wp-admin/includes/plugin.php';
@mkdir(WP_PLUGIN_DIR . '/evander-wordpress');
foreach (glob('/companion/*.php') as $file) { copy($file, WP_PLUGIN_DIR . '/evander-wordpress/' . basename($file)); }
@mkdir(WP_PLUGIN_DIR . '/evander-fixture');
copy('/fixtures/fixture.php', WP_PLUGIN_DIR . '/evander-fixture/fixture.php');
activate_plugin('evander-wordpress/evander-wordpress.php');
activate_plugin('evander-fixture/fixture.php');
evander_fixture_register_types();
symlink('/theme-releases/current', WP_CONTENT_DIR . '/themes/evander-fixture-theme');
switch_theme('evander-fixture-theme');
$user = get_user_by('login','designer');
$password = WP_Application_Passwords::create_new_application_password($user->ID, ['name'=>'fixture']);
add_role('evander_fixture_designer','Restricted fixture designer',[
    'read'=>true,'edit_theme_options'=>true,'edit_posts'=>true,'edit_pages'=>true,
    'edit_published_posts'=>true,'edit_published_pages'=>true,
    'edit_others_posts'=>true,'edit_others_pages'=>true,'publish_posts'=>true,'publish_pages'=>true]);
$restricted = wp_create_user('restricted-designer','fixture-only-restricted-password','restricted@example.test');
$restricted_user = new WP_User($restricted); $restricted_user->set_role('evander_fixture_designer');
$restricted_password = WP_Application_Passwords::create_new_application_password($restricted,['name'=>'fixture']);
add_role('evander_fixture_appearance','Appearance-only fixture account',['read'=>true,'edit_theme_options'=>true]);
$appearance = wp_create_user('appearance-designer','fixture-only-appearance-password','appearance@example.test');
(new WP_User($appearance))->set_role('evander_fixture_appearance');
$appearance_password = WP_Application_Passwords::create_new_application_password($appearance,['name'=>'fixture']);
add_role('evander_fixture_editor','Scoped fixture editor',['read'=>true,'edit_theme_options'=>true,
    'edit_posts'=>true,'edit_pages'=>true,'edit_published_pages'=>true]);
$editor = wp_create_user('scoped-editor','fixture-only-editor-password','editor@example.test');
(new WP_User($editor))->set_role('evander_fixture_editor');
$editor_password = WP_Application_Passwords::create_new_application_password($editor,['name'=>'fixture']);
$owned = wp_insert_post(['post_author'=>$editor,'post_type'=>'page','post_status'=>'draft','post_title'=>'Owned draft']);
$owned_other = wp_insert_post(['post_author'=>$editor,'post_type'=>'page','post_status'=>'draft','post_title'=>'Owned extension']);
$owned_private = wp_insert_post(['post_author'=>$editor,'post_type'=>'page','post_status'=>'private','post_title'=>'Owned private page']);
$private = wp_insert_post(['post_author'=>$user->ID,'post_type'=>'page','post_status'=>'private','post_title'=>'Private other-author page','post_password'=>'synthetic-page-password']);
add_post_meta($owned,'_fixture_locked','Restricted metadata');
add_post_meta($owned,'fixture_no_add','Before'); add_post_meta($owned,'fixture_no_delete','Before');
$post = wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Before','post_content'=>'Welcome']);
$other = wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Untouched language','post_content'=>'Välkommen']);
$future = wp_insert_post(['post_type'=>'page','post_status'=>'future','post_date'=>gmdate('Y-m-d H:i:s',time()+86400),'post_title'=>'Scheduled page']);
$attachment = wp_insert_post(['post_type'=>'attachment','post_status'=>'inherit','post_title'=>'Attachment metadata','post_mime_type'=>'image/png']);
add_post_meta($attachment,'_wp_attached_file','2026/synthetic-image.png');
add_post_meta($post,'fixture_caption','Before'); add_post_meta($post,'_fixture_caption','field_fixture_caption');
add_post_meta($post,'fixture_rows_0_child','Nested before'); add_post_meta($post,'_fixture_rows_0_child','field_fixture_child');
$autodraft = wp_insert_post(['post_type'=>'page','post_status'=>'auto-draft','post_title'=>'Draft page']);
$captured_html = wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Captured HTML']);
global $wpdb;
$wpdb->update($wpdb->posts,['post_content'=>'<script>window.capturedFixture=true</script><p>Captured baseline</p>'],['ID'=>$captured_html]);
update_option('fixture_payment_api_key','synthetic-persistent-payment-secret');
add_post_meta($post, '_builder_data', ['heading'=>'Before']);
update_option('permalink_structure','/%postname%/');
flush_rewrite_rules();
$menu=wp_insert_term('Synthetic menu','nav_menu');
$second_menu=wp_insert_term('Second menu','nav_menu');
add_term_meta($menu['term_id'], '_design_caption', ['caption'=>'Before']);
wp_set_object_terms($post,[$menu['term_id']],'nav_menu');
wp_set_object_terms($other,[$menu['term_id']],'nav_menu');
echo json_encode(['password'=>$password[0],'restricted_password'=>$restricted_password[0],'restricted_user'=>$restricted,
    'appearance_password'=>$appearance_password[0],'editor_password'=>$editor_password[0],'editor_user'=>$editor,
    'owned'=>$owned,'owned_other'=>$owned_other,'owned_private'=>$owned_private,'private'=>$private,
    'captured_html'=>$captured_html,'post'=>$post,'other'=>$other,'future'=>$future,'attachment'=>$attachment,'autodraft'=>$autodraft,
    'menu'=>$menu['term_taxonomy_id'],'menu_term'=>$menu['term_id'],'second_menu'=>$second_menu['term_taxonomy_id']]);
