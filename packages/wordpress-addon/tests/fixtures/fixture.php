<?php
/** Plugin Name: Synthetic transaction fixture */
// Synthetic field definitions exercise the optional ACF boundary on real WP metadata.
function get_field_object($selector,$post_id=false,$format_value=true,$load_value=true) {
    if (get_option('fixture_hide_fields')) { return false; }
    $fields=[
        'field_fixture_caption'=>['key'=>'field_fixture_caption','name'=>'fixture_caption','type'=>'text'],
        'field_fixture_new'=>['key'=>'field_fixture_new','name'=>'fixture_new','type'=>'text'],
        'field_fixture_child'=>['key'=>'field_fixture_child','name'=>'child','type'=>'text'],
        'field_fixture_attachment'=>['key'=>'field_fixture_attachment','name'=>'wp_attached_file','type'=>'text']];
    if (isset($fields[$selector])) { return $fields[$selector]; }
    $reference=get_post_meta($post_id,'_'.$selector,true);
    if (is_string($reference) && isset($fields[$reference])) { return array_merge($fields[$reference],['name'=>$selector]); }
    return false;
}
function evander_fixture_register_types() {
    register_post_type('fixture_records',['public'=>false,'show_ui'=>true,'map_meta_cap'=>true,
        'capability_type'=>['fixture_record','fixture_records']]);
    register_post_type('fixture_content',['public'=>false,'show_ui'=>true,'map_meta_cap'=>true,'capability_type'=>'post']);
    register_taxonomy('fixture_design','page',['public'=>false,
        'capabilities'=>['manage_terms'=>'manage_categories','edit_terms'=>'manage_categories',
            'delete_terms'=>'manage_categories','assign_terms'=>'assign_fixture_design']]);
    foreach (['post','term'] as $type) {
        register_meta($type,'_fixture_locked',['auth_callback'=>function(){ return current_user_can('manage_options'); }]);
    }
    foreach (['add','delete'] as $operation) {
        register_post_meta('page','fixture_no_'.$operation,['auth_callback'=>function($allowed,$key,$id,$user,$cap) use ($operation) {
            return $cap!==$operation.'_post_meta';
        }]);
    }
    register_post_meta('page','_fixture_new',['auth_callback'=>function($allowed,$key,$id,$user,$cap) {
        return $cap!=='add_post_meta' || !get_option('fixture_deny_reference_add');
    }]);
    register_post_meta('page','fixture_new_no_delete',['auth_callback'=>function($allowed,$key,$id,$user,$cap){ return $cap!=='delete_post_meta'; }]);
    add_filter('auth_post_meta_fixture_hook_locked',function(){ return current_user_can('manage_options'); });
}
add_action('init','evander_fixture_register_types');
add_filter('map_meta_cap',function($caps,$cap,$user,$args){
    if ($cap==='assign_term' && (int)($args[0]??0)===(int)get_option('fixture_denied_term')) { return ['do_not_allow']; }
    if (str_ends_with($cap,'_post_meta') && ($args[1]??'')==='_builder_data' && get_option('fixture_deny_builder')) { return ['do_not_allow']; }
    return $caps;
},10,4);
add_filter('evander_design_taxonomies',function($taxonomies){ $taxonomies[]='fixture_design'; return $taxonomies; });
add_filter('evander_design_metadata',function($keys,$type){
    $keys[]=$type==='post' ? '_builder_data' : '_design_caption'; return $keys;
},10,2);
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
add_action('wp_ajax_evander_fixture_raw_post',function(){
    global $wpdb;
    $field=$_POST['field'];
    if (!in_array($field,['post_status','post_author','post_type','post_title','post_content'],true)) { wp_die('Invalid','',['response'=>400]); }
    $wpdb->update($wpdb->posts,[$field=>wp_unslash($_POST['value'])],['ID'=>(int)$_POST['id']]);
    wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_raw_parent',function(){
    global $wpdb;
    $wpdb->update($wpdb->posts,['post_parent'=>(int)$_POST['parent']],['ID'=>(int)$_POST['id']]);
    wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_acf',function(){
    update_post_meta((int)$_POST['id'],$_POST['field'],$_POST['value']); wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_move_meta',function(){
    global $wpdb;
    $meta_id=$wpdb->get_var($wpdb->prepare("SELECT meta_id FROM $wpdb->postmeta WHERE post_id=%d AND meta_key='_builder_data'",(int)$_POST['id']));
    $wpdb->update($wpdb->postmeta,['post_id'=>(int)$_POST['other']],['meta_id'=>$meta_id]);
    wp_send_json(['saved'=>true]);
});
add_action('wp_ajax_evander_fixture_nested_redeem',function(){
    $marker=$_POST['marker']??'';
    if (!preg_match('/^[a-f0-9]{32}$/D',$marker)) { wp_die('Denied','',['response'=>403]); }
    $request=new WP_REST_Request('POST','/evander/v1/browser/redeem');
    $request->set_header('content-type','application/json');
    $request->set_body(wp_json_encode(['ticket'=>str_repeat('0',64)]));
    $result=Evander_WordPress::handle('browser/redeem',$request);
    file_put_contents('/tmp/evander-fixture-nested-'.$marker,'ready');
    // A separate database connection attempts a human edit during this window.
    usleep(500000);
    wp_send_json(['status'=>is_wp_error($result) ? $result->get_error_data()['status'] : 200]);
});
