<?php
/** Plugin Name: Synthetic transaction fixture */
add_filter('query',function($sql){
    if (isset($_COOKIE[LOGGED_IN_COOKIE]) && preg_match('/^\s*(INSERT|UPDATE|DELETE|REPLACE)\b/i',$sql)) {
        preg_match('/WHERE `option_name` = \'([a-zA-Z0-9_-]+)\'/',$sql,$option);
        error_log('fixture write: '.preg_replace('/\'(?:[^\'\\\\]|\\\\.)*\'/s',"'<redacted>'",$sql).' option='.($option[1]??''));
    }
    return $sql;
}, PHP_INT_MAX-1);
add_filter('evander_browser_compatible_plugins',function($plugins){ $plugins[]='evander-fixture/fixture.php'; return $plugins; });
add_action('wp_ajax_evander_fixture_nonce',function(){ wp_send_json(['nonce'=>wp_create_nonce('wp_rest')]); });
add_action('wp_ajax_evander_fixture_save',function(){
    $id=(int)$_POST['id'];
    if (!current_user_can('edit_post',$id)) { wp_die('Denied','',['response'=>403]); }
    wp_update_post(['ID'=>$id,'post_title'=>sanitize_text_field($_POST['title'])]);
    wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_uncovered',function(){
    update_option('blogname','Uncovered write'); wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_meta',function(){
    update_post_meta((int)$_POST['id'],'_builder_data',['heading'=>sanitize_text_field($_POST['heading'])]);
    wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_move_meta',function(){
    global $wpdb;
    $meta_id=$wpdb->get_var($wpdb->prepare("SELECT meta_id FROM $wpdb->postmeta WHERE post_id=%d AND meta_key='_builder_data'",(int)$_POST['id']));
    $wpdb->update($wpdb->postmeta,['post_id'=>(int)$_POST['other']],['meta_id'=>$meta_id]);
    wp_send_json(['saved'=>true]);
});
