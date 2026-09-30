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
symlink('/theme-releases/current', WP_CONTENT_DIR . '/themes/evander-fixture-theme');
switch_theme('evander-fixture-theme');
$user = get_user_by('login','designer');
$password = WP_Application_Passwords::create_new_application_password($user->ID, ['name'=>'fixture']);
$post = wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Before','post_content'=>'Welcome']);
$other = wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Untouched language','post_content'=>'Välkommen']);
add_post_meta($post, '_builder_data', ['heading'=>'Before']);
update_option('permalink_structure','/%postname%/');
flush_rewrite_rules();
$menu=wp_insert_term('Synthetic menu','nav_menu');
$second_menu=wp_insert_term('Second menu','nav_menu');
wp_set_object_terms($post,[$menu['term_id']],'nav_menu');
wp_set_object_terms($other,[$menu['term_id']],'nav_menu');
echo json_encode(['password'=>$password[0], 'post'=>$post,'other'=>$other,'menu'=>$menu['term_taxonomy_id'],'second_menu'=>$second_menu['term_taxonomy_id']]);
