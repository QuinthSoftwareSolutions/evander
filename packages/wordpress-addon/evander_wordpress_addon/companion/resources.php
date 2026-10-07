<?php
/** Adapters must cover every record they mutate and participate in the request transaction. */
interface Evander_Resource_Adapter {
    public function supports($key);
    public function discover();
    public function read($key);
    public function validate($key, $value);
    public function lock($key);
    public function write($key, $value);
    public function allows_query($key, $sql);
}

final class Evander_Core_Resources implements Evander_Resource_Adapter {
    private function split($key) {
        if (!preg_match('/^(post|meta|option|term|termmeta|acf|relationships):([a-zA-Z0-9_.-]+)(?::([a-zA-Z0-9_.-]+))?$/D', $key, $matches)) { throw new RuntimeException('Invalid resource key', 400); }
        return [$matches[1], $matches[2], $matches[3] ?? null];
    }
    public function supports($key) { return (bool)preg_match('/^(post|meta|option|term|termmeta|acf|relationships):/', $key); }
    public function discover() {
        return ['id'=>'core', 'resources'=>['post','meta','option','term','termmeta','acf','relationships'],
            'post_fields'=>$this->post_fields(), 'options'=>$this->options(), 'browser_guard'=>true,
            'notes'=>'Posts include blocks, templates, global styles, navigation, menu items, custom CSS and media metadata. ACF snapshots include field references. Use source deployment for files.'];
    }
    private function post_fields() { return ['post_author','post_date','post_date_gmt','post_title','post_content','post_excerpt','post_status','comment_status','ping_status','post_password','post_name','to_ping','pinged','post_modified','post_modified_gmt','post_content_filtered','post_type','post_parent','guid','menu_order','post_mime_type','comment_count']; }
    private function options() {
        // No credentials, site origins, administrators, plugin activation or security settings.
        return apply_filters('evander_design_options', ['theme_mods_' . get_stylesheet(), 'evander_restore_probe']);
    }
    public function validate($key, $value) {
        [$kind,$id,$field] = $this->split($key);
        if ($kind === 'option' && !in_array($id, $this->options(), true)) { throw new RuntimeException('Option is not approved for design editing', 400); }
        if ($kind === 'option' && $value !== null && (!is_array($value) || !array_key_exists('value',$value) || count($value)!==2 || !in_array($value['autoload']??null,['yes','no','on','off','auto','auto-on','auto-off'],true))) { throw new RuntimeException('Option value and autoload mode required',400); }
        if (in_array($kind,['meta','termmeta','acf'],true) && (!$field || !ctype_digit($id))) { throw new RuntimeException('Numeric record and field required',400); }
        if (in_array($kind,['meta','termmeta'],true) && $value !== null && (!is_array($value) || !array_is_list($value))) { throw new RuntimeException('Metadata requires a list of stored values',400); }
        if (in_array($kind,['post','term'],true) && !ctype_digit($id) && !($kind==='post' && preg_match('/^new-[a-zA-Z0-9_-]+$/D',$id))) { throw new RuntimeException('Invalid record ID',400); }
        if ($kind === 'post' && $value !== null) {
            if (!is_array($value) || array_diff(array_keys($value),$this->post_fields())) { throw new RuntimeException('Unsupported post fields',400); }
            if (isset($value['post_type']) && !get_post_type_object($value['post_type'])) { throw new RuntimeException('Unknown post type',400); }
            if (isset($value['post_status']) && !get_post_status_object($value['post_status'])) { throw new RuntimeException('Unsupported post status',400); }
        }
        if ($kind === 'term' && $value !== null && (!is_array($value) || array_diff(array_keys($value),['name','slug','term_group']))) { throw new RuntimeException('Unsupported term fields',400); }
        if ($kind === 'acf') {
            if (!function_exists('get_field_object')) { throw new RuntimeException('ACF adapter unavailable',400); }
            if ($value !== null && (!is_array($value) || count($value)!==3 || !array_key_exists('value',$value) ||
                !is_string($value['reference']??null) || !is_bool($value['exists']??null))) {
                throw new RuntimeException('ACF value, reference and existence required',400);
            }
        }
        if ($kind === 'relationships') {
            if (!ctype_digit($id) || !$field || !in_array($field,apply_filters('evander_design_taxonomies',['nav_menu']),true) || !taxonomy_exists($field)) { throw new RuntimeException('Taxonomy is not approved for design editing',400); }
            if ($value !== null) {
                if (!is_array($value) || !array_is_list($value) || count($value)>200) { throw new RuntimeException('Relationship list required',400); }
                global $wpdb; $seen=[];
                foreach ($value as $row) {
                    if (!is_array($row) || array_diff(array_keys($row),['term_taxonomy_id','term_order']) || !isset($row['term_taxonomy_id'])) { throw new RuntimeException('Invalid relationship',400); }
                    $tax_id=(int)$row['term_taxonomy_id'];
                    if (isset($seen[$tax_id]) || $wpdb->get_var($wpdb->prepare("SELECT taxonomy FROM $wpdb->term_taxonomy WHERE term_taxonomy_id=%d",$tax_id)) !== $field) { throw new RuntimeException('Relationship taxonomy mismatch',400); }
                    $seen[$tax_id]=true;
                }
            }
        }
        if (in_array($kind,['meta','termmeta','acf'],true)) {
            foreach ($this->metadata_keys($kind,$field) as $name) {
                // SQL collations may compare metadata names case-insensitively.
                if (strcasecmp($name,'_wp_attached_file')===0 || preg_match('/password|secret|token|api_key/i',$name)) { throw new RuntimeException('Protected metadata',400); }
            }
        }
    }
    private function metadata_keys($kind,$field) { return $kind==='acf' ? [$field,'_'.$field] : [$field]; }
    private function require_cap($cap,...$args) {
        if (!current_user_can($cap,...$args)) { throw new RuntimeException('Resource permission denied',403); }
    }
    private function require_post_cap($operation,$id) {
        $post=get_post($id); $type=$post ? get_post_type_object($post->post_type) : null;
        if (!$type) { throw new RuntimeException('Post does not exist',403); }
        $this->require_cap($type->cap->$operation,$id);
    }
    public function authorize_read($key,$require_existing=false) {
        [$kind,$id,$field]=$this->split($key); $this->validate($key,null);
        if ($kind==='option') { $this->require_cap('edit_theme_options'); }
        elseif ($kind==='post') {
            // Inspection returns raw edit fields, including passwords and private HTML.
            if (ctype_digit($id) && get_post((int)$id)) { $this->require_post_cap('edit_post',(int)$id); }
            elseif ($require_existing) { throw new RuntimeException('Captured post is no longer accessible',403); }
        } elseif ($kind==='term') {
            if (get_term((int)$id) || $this->read($key)!==null) { $this->require_cap('edit_term',(int)$id); }
        } elseif ($kind==='relationships') {
            $this->require_post_cap('edit_post',(int)$id);
            $this->require_cap(get_taxonomy($field)->cap->assign_terms,(int)$id);
        } else {
            $type=$kind==='termmeta' ? 'term' : 'post';
            if ($type==='post') { $this->require_post_cap('edit_post',(int)$id); }
            else { $this->require_cap('edit_term',(int)$id); }
            if ($kind==='acf') { $this->validate_acf_edit((int)$id,$field,$this->read($key)); }
            foreach ($this->metadata_keys($kind,$field) as $name) {
                $reference=$kind==='acf' && $name==='_'.$field || $kind==='meta' && $this->acf_reference_field((int)$id,$name)!==null;
                $this->authorize_metadata($type,(int)$id,$name,['edit'],$reference);
            }
        }
    }
    private function authorize_metadata($type,$id,$field,$operations,$reference=false,$writing=false) {
        global $wpdb,$wp_filter;
        $subtype=get_object_subtype($type,$id);
        $table=$type==='post' ? $wpdb->postmeta : $wpdb->termmeta; $column=$type==='post' ? 'post_id' : 'term_id';
        // Check stored and registered names as well as the request spelling: the
        // database can address the same metadata through a differently cased key.
        $names=$wpdb->get_col($wpdb->prepare("SELECT DISTINCT meta_key FROM $table WHERE $column=%d AND meta_key=%s",$id,$field));
        $registered=array_merge(get_registered_meta_keys($type),get_registered_meta_keys($type,$subtype));
        foreach (array_keys($registered) as $name) { if (strcasecmp($name,$field)===0) { $names[]=$name; } }
        foreach ([['auth_'.$type.'_meta_',''],['auth_'.$type.'_meta_','_for_'.$subtype],['auth_'.$type.'_'.$subtype.'_meta_','']] as [$prefix,$suffix]) {
            foreach (array_keys($wp_filter) as $hook) {
                if (strcasecmp($hook,$prefix.$field.$suffix)===0) { $names[]=substr($hook,strlen($prefix),strlen($hook)-strlen($prefix)-strlen($suffix)); }
            }
        }
        if (!$names) { $names=[$field]; }
        $aliased=false;
        $approved=apply_filters('evander_design_metadata',$type==='post' ? ['_edit_lock','_edit_last'] : [],$type,$subtype);
        foreach (array_unique($names) as $name) {
            if ($name!==$field) { $aliased=true; }
            $design=$reference;
            foreach ($approved as $candidate) { if (strcasecmp($name,$candidate)===0) { $design=true; } }
            // These values participate in WordPress's deletion capability mapping.
            if (in_array(strtolower($name),['_wp_trash_meta_status','_wp_trash_meta_time'],true)) { $design=false; }
            // Lift only WP's default underscore protection for explicitly approved
            // design keys. Registered callbacks and object-specific policies still run.
            $unprotect=function($protected,$key,$object_type) use ($design,$type,$name) {
                return $design && $object_type===$type && $key===$name ? false : $protected;
            };
            add_filter('is_protected_meta',$unprotect,PHP_INT_MAX,3);
            try { foreach ($operations as $operation) { $this->require_cap($operation.'_'.$type.'_meta',$id,$name); } }
            finally { remove_filter('is_protected_meta',$unprotect,PHP_INT_MAX); }
        }
        if ($writing && $aliased) { throw new RuntimeException('Use the stored or registered metadata spelling',400); }
    }
    private function acf_reference_field($id,$field,$values=[]) {
        global $wpdb;
        if (!str_starts_with($field,'_') || !function_exists('get_field_object')) { return null; }
        $keys=array_unique(array_merge([$field],$wpdb->get_col($wpdb->prepare("SELECT DISTINCT meta_key FROM $wpdb->postmeta WHERE post_id=%d AND meta_key=%s",$id,$field))));
        foreach ($keys as $key) {
            $name=substr($key,1); $stored=$this->read('meta:'.$id.':'.$key);
            foreach (array_merge($stored,$values??[]) as $reference) {
                if (!is_string($reference) || !str_starts_with($reference,'field_')) { continue; }
                $definition=get_field_object($reference,$id,false,false);
                if (is_array($definition) && strcasecmp($definition['name']??'',$name)===0) {
                    if ($key!==$field || $definition['name']!==$name) { throw new RuntimeException('ACF field spelling mismatch',400); }
                    return $name;
                }
                if (is_array($definition) && in_array($reference,$stored,true) && str_ends_with($name,'_'.($definition['name']??''))) {
                    $bound=get_field_object($name,$id,false,false);
                    if (is_array($bound) && ($bound['name']??null)===$name && ($bound['key']??null)===$reference) {
                        if ($key!==$field) { throw new RuntimeException('ACF field spelling mismatch',400); }
                        return $name;
                    }
                }
            }
        }
        return null;
    }
    private function validate_acf_reference($id,$field,$values) {
        if ($values===null || !$values) {
            if ($this->read('meta:'.$id.':'.$field)) { throw new RuntimeException('ACF value still requires its reference',400); }
            return;
        }
        if (count($values)!==1 || !is_string($values[0])) { throw new RuntimeException('ACF field reference required',400); }
        $this->validate_acf_edit($id,$field,['value'=>'','reference'=>$values[0],'exists'=>true]);
    }
    private function new_post_defaults() {
        return ['post_author'=>get_current_user_id(),'post_date'=>current_time('mysql'),
            'post_date_gmt'=>current_time('mysql',true),'post_status'=>'draft','post_type'=>'page'];
    }
    private function authorize_post($id,$value) {
        if (ctype_digit($id)) { wp_cache_delete((int)$id,'posts'); }
        $before=ctype_digit($id) ? get_post((int)$id) : null;
        if ($value===null) {
            if (!$before) { throw new RuntimeException('Post does not exist',403); }
            $this->require_post_cap('delete_post',(int)$id); return;
        }
        if ($before) { $this->require_post_cap('edit_post',(int)$id); }
        $effective=array_merge($before ? $before->to_array() : $this->new_post_defaults(),$value);
        $type=get_post_type_object($effective['post_type']);
        if (!$type) { throw new RuntimeException('Unknown post type',400); }
        $changed_type=$before && $before->post_type!==$effective['post_type'];
        if (!$before || $changed_type) {
            $this->require_cap($type->cap->create_posts);
            if ($changed_type) { $this->require_cap($type->cap->edit_posts); }
            if ($changed_type && !$type->map_meta_cap) { $this->require_cap($type->cap->edit_post,(int)$id); }
        }
        $parent=$effective['post_parent']??0;
        if (!preg_match('/^[0-9]+$/D',(string)$parent)) { throw new RuntimeException('Invalid post parent',400); }
        if ($effective['post_type']==='revision' && !(int)$parent) { throw new RuntimeException('Revision parent required',400); }
        if ((int)$parent && (!$before || $changed_type || (int)$before->post_parent!==(int)$parent)) {
            $this->require_post_cap(in_array($effective['post_type'],['revision','attachment'],true) ? 'edit_post' : 'read_post',(int)$parent);
        }
        if (!preg_match('/^[0-9]+$/D',(string)$effective['post_author'])) { throw new RuntimeException('Invalid post author',400); }
        if (!$before || (int)$before->post_author!==(int)$effective['post_author']) {
            if (!get_user_by('id',(int)$effective['post_author'])) { throw new RuntimeException('Invalid post author',400); }
        }
        if ((!$before || $changed_type || (int)$before->post_author!==(int)$effective['post_author']) &&
            (int)$effective['post_author']!==get_current_user_id()) { $this->require_cap($type->cap->edit_others_posts); }
        $status=$effective['post_status']; $status_object=get_post_status_object($status);
        if (!$status_object) { throw new RuntimeException('Unsupported post status',400); }
        if ((!$before || $changed_type || $before->post_status!==$status) &&
            ($status_object->public || $status_object->private || $status==='future')) { $this->require_cap($type->cap->publish_posts); }
        if ($changed_type) {
            if ($status_object->public || $status==='future') { $this->require_cap($type->cap->edit_published_posts); }
        }
        if ($before && ($changed_type || (int)$before->post_author!==(int)$effective['post_author']) &&
            $status_object->private && (int)$effective['post_author']!==get_current_user_id()) { $this->require_cap($type->cap->edit_private_posts); }
        if ($before && $status==='trash' && $before->post_status!=='trash') { $this->require_post_cap('delete_post',(int)$id); }
    }
    private function validate_acf_edit($id,$field,$value) {
        // Definition checks apply to new writes, not exact captured restoration.
        $reference=$value!==null && ($value['exists'] || $value['reference']!=='') ? $value['reference'] : get_post_meta($id,'_'.$field,true);
        if (!$reference && ($value===null || !$value['exists']) && !metadata_exists('post',$id,$field)) { return; }
        $definition=get_field_object($reference,$id,false,false);
        $bound=get_field_object($field,$id,false,false);
        $existing_nested=is_array($definition) && str_ends_with($field,'_'.($definition['name']??'')) &&
            is_array($bound) && ($bound['name']??null)===$field && ($bound['key']??null)===$reference && get_post_meta($id,'_'.$field,true)===$reference;
        if (!is_array($definition) || (($definition['name']??null)!==$field && !$existing_nested) || ($definition['key']??null)!==$reference) {
            throw new RuntimeException('ACF field reference mismatch',400);
        }
    }
    public function validate_edit($key, $value) {
        [$kind,$id,$field] = $this->split($key); $this->validate($key,$value);
        if ($kind==='post') { $this->authorize_post($id,$value); }
        elseif ($kind==='term') { $this->require_cap($value===null ? 'delete_term' : 'edit_term',(int)$id); }
        else { $this->authorize_read($key); }
        if (in_array($kind,['meta','termmeta','acf'],true)) {
            $type=$kind==='termmeta' ? 'term' : 'post';
            foreach ($this->metadata_keys($kind,$field) as $name) {
                $operations=['edit'];
                if ($this->read(($type==='post' ? 'meta:' : 'termmeta:').$id.':'.$name)) { $operations[]='delete'; }
                $inserts=$value!==null && ($kind==='acf' ? ($name===$field ? $value['exists'] : $value['exists'] || $value['reference']!=='') : count($value)>0);
                if ($inserts) { $operations[]='add'; }
                $reference=$kind==='acf' && $name==='_'.$field || $kind==='meta' && $this->acf_reference_field((int)$id,$name,$value)!==null;
                $this->authorize_metadata($type,(int)$id,$name,$operations,$reference,true);
            }
            if ($kind==='acf') { $this->validate_acf_edit((int)$id,$field,$value); }
            elseif ($kind==='meta') {
                $reference=$this->acf_reference_field((int)$id,$field,$value);
                if ($reference!==null) { $this->validate_acf_reference((int)$id,$reference,$value); }
            }
        }
        if ($kind==='relationships') {
            global $wpdb;
            foreach (($value??[]) as $row) {
                $term_id=$wpdb->get_var($wpdb->prepare("SELECT term_id FROM $wpdb->term_taxonomy WHERE term_taxonomy_id=%d",$row['term_taxonomy_id']));
                $this->require_cap('assign_term',(int)$term_id);
            }
        }
        if ($kind !== 'post' || $value === null || current_user_can('unfiltered_html')) { return; }
        foreach (['post_title','post_content','post_excerpt','post_content_filtered'] as $field) {
            // Entity normalization is harmless (for example R&D -> R&amp;D).
            // Reject removed tags, attributes and protocols without changing the proposal.
            if (isset($value[$field]) && (!is_string($value[$field]) ||
                wp_kses_post($value[$field]) !== wp_kses_normalize_entities($value[$field]))) {
                throw new RuntimeException('Post HTML exceeds the service account permissions',403);
            }
        }
    }
    public function read($key) {
        global $wpdb; [$kind,$id,$field] = $this->split($key); $this->validate($key,null);
        if ($kind === 'post') {
            if (!ctype_digit($id)) { return null; }
            $row = $wpdb->get_row($wpdb->prepare("SELECT * FROM $wpdb->posts WHERE ID=%d",$id),ARRAY_A);
            return $row ? array_intersect_key($row,array_flip($this->post_fields())) : null;
        }
        if ($kind === 'option') {
            $row = $wpdb->get_row($wpdb->prepare("SELECT option_value,autoload FROM $wpdb->options WHERE option_name=%s",$id),ARRAY_A);
            return $row === null ? null : ['value'=>maybe_unserialize($row['option_value']),'autoload'=>$row['autoload']];
        }
        if ($kind === 'term') {
            return $wpdb->get_row($wpdb->prepare("SELECT name,slug,term_group FROM $wpdb->terms WHERE term_id=%d",$id),ARRAY_A) ?: null;
        }
        if ($kind === 'acf') {
            $values=$this->read('meta:'.$id.':'.$field); $references=$this->read('meta:'.$id.':_'.$field);
            if (count($values)>1 || count($references)>1) { throw new RuntimeException('ACF requires single stored values',400); }
            $value=['value'=>$values ? $values[0] : '', 'reference'=>$references ? $references[0] : '', 'exists'=>count($values)>0];
            $this->validate($key,$value); return $value;
        }
        if ($kind === 'relationships') {
            return $wpdb->get_results($wpdb->prepare("SELECT r.term_taxonomy_id,r.term_order FROM $wpdb->term_relationships r INNER JOIN $wpdb->term_taxonomy t ON t.term_taxonomy_id=r.term_taxonomy_id WHERE r.object_id=%d AND t.taxonomy=%s ORDER BY r.term_taxonomy_id",$id,$field),ARRAY_A);
        }
        $table = $kind === 'meta' ? $wpdb->postmeta : $wpdb->termmeta;
        $column = $kind === 'meta' ? 'post_id' : 'term_id';
        return array_map('maybe_unserialize',$wpdb->get_col($wpdb->prepare("SELECT meta_value FROM $table WHERE $column=%d AND meta_key=%s ORDER BY meta_id",$id,$field)));
    }
    public function lock($key) {
        global $wpdb; [$kind,$id,$field] = $this->split($key); $this->validate($key,null);
        if ($kind === 'post' && ctype_digit($id)) { $wpdb->query($wpdb->prepare("SELECT ID FROM $wpdb->posts WHERE ID=%d FOR UPDATE",$id)); }
        elseif ($kind === 'option') { $wpdb->query($wpdb->prepare("SELECT option_id FROM $wpdb->options WHERE option_name=%s FOR UPDATE",$id)); }
        elseif ($kind === 'term') { $wpdb->query($wpdb->prepare("SELECT term_id FROM $wpdb->terms WHERE term_id=%d FOR UPDATE",$id)); }
        elseif ($kind === 'relationships') {
            $wpdb->query($wpdb->prepare("SELECT ID FROM $wpdb->posts WHERE ID=%d FOR UPDATE",$id));
            $wpdb->query($wpdb->prepare("SELECT term_taxonomy_id FROM $wpdb->term_taxonomy WHERE taxonomy=%s FOR UPDATE",$field));
            $wpdb->query($wpdb->prepare("SELECT term_taxonomy_id FROM $wpdb->term_relationships WHERE object_id=%d FOR UPDATE",$id));
        }
        elseif (in_array($kind,['meta','acf','termmeta'],true)) {
            $table = $kind==='termmeta' ? $wpdb->termmeta : $wpdb->postmeta; $column = $kind==='termmeta' ? 'term_id' : 'post_id';
            // Lock the parent as well so insertions cannot race a missing metadata row.
            $parent = $kind==='termmeta' ? $wpdb->terms : $wpdb->posts; $pk = $kind==='termmeta' ? 'term_id' : 'ID';
            $wpdb->query($wpdb->prepare("SELECT $pk FROM $parent WHERE $pk=%d FOR UPDATE",$id));
            $wpdb->query($wpdb->prepare("SELECT meta_id FROM $table WHERE $column=%d AND meta_key IN (%s,%s) FOR UPDATE",$id,$field,'_'.$field));
        }
    }
    public function write($key, $value) {
        global $wpdb; [$kind,$id,$field] = $this->split($key); $this->validate($key,$value);
        if ($kind === 'post') {
            if ($value === null) {
                // Do not delete unrelated metadata, translation links or media files implicitly.
                $this->require_write($wpdb->delete($wpdb->posts,['ID'=>(int)$id])); clean_post_cache((int)$id); return $key;
            }
            if (ctype_digit($id)) {
                if ($this->read($key) === null) { $this->require_write($wpdb->insert($wpdb->posts,array_merge($this->new_post_defaults(),['ID'=>(int)$id],$value))); }
                else { $this->require_write($wpdb->update($wpdb->posts,$value,['ID'=>(int)$id])); }
                clean_post_cache((int)$id);
            }
            else {
                $value = array_merge($this->new_post_defaults(), $value);
                $this->require_write($wpdb->insert($wpdb->posts,$value));
                $key = 'post:' . $wpdb->insert_id;
            }
        } elseif ($kind === 'option') {
            $this->require_write($wpdb->delete($wpdb->options,['option_name'=>$id]));
            if ($value !== null) { $this->require_write($wpdb->insert($wpdb->options,['option_name'=>$id,'option_value'=>maybe_serialize($value['value']),'autoload'=>$value['autoload']])); }
            wp_cache_delete($id,'options'); wp_cache_delete('alloptions','options'); wp_cache_delete('notoptions','options');
        } elseif ($kind === 'term') {
            if ($value===null) { $this->require_write($wpdb->delete($wpdb->terms,['term_id'=>(int)$id])); }
            elseif ($this->read($key) === null) { $this->require_write($wpdb->insert($wpdb->terms,array_merge(['term_id'=>(int)$id],$value))); }
            else { $this->require_write($wpdb->update($wpdb->terms,$value,['term_id'=>(int)$id])); }
            clean_term_cache((int)$id);
        }
        elseif ($kind === 'relationships') {
            $previous=$this->read($key);
            $this->require_write($wpdb->query($wpdb->prepare("DELETE r FROM $wpdb->term_relationships r INNER JOIN $wpdb->term_taxonomy t ON t.term_taxonomy_id=r.term_taxonomy_id WHERE r.object_id=%d AND t.taxonomy=%s",$id,$field)));
            foreach (($value??[]) as $row) { $this->require_write($wpdb->insert($wpdb->term_relationships,['object_id'=>(int)$id,'term_taxonomy_id'=>(int)$row['term_taxonomy_id'],'term_order'=>(int)($row['term_order']??0)])); }
            $ids=array_unique(array_merge(array_column($previous,'term_taxonomy_id'),array_column($value??[],'term_taxonomy_id')));
            if ($ids) { wp_update_term_count_now($ids,$field); }
            clean_object_term_cache((int)$id,get_post_type((int)$id));
        }
        elseif ($kind === 'acf') {
            $exists = $value !== null && !empty($value['exists']);
            $this->write_metadata('post',(int)$id,$field,$exists ? [$value['value']] : []);
            $reference=$value['reference']??'';
            $this->write_metadata('post',(int)$id,'_'.$field,$exists || $reference!=='' ? [$reference] : []);
        } else {
            $this->write_metadata($kind==='meta' ? 'post' : 'term',(int)$id,$field,$value ?? []);
        }
        return $key;
    }
    private function require_write($result) {
        // Zero affected rows is a valid no-op; false is a database failure.
        if ($result === false) { throw new RuntimeException('Resource write failed'); }
    }
    private function write_metadata($type, $id, $field, $values) {
        global $wpdb;
        $table = $type==='post' ? $wpdb->postmeta : $wpdb->termmeta;
        $column = $type==='post' ? 'post_id' : 'term_id';
        // Metadata APIs conflate missing rows and failed deletion. Use exact results.
        $this->require_write($wpdb->delete($table,[$column=>$id,'meta_key'=>$field]));
        foreach ($values as $item) { $this->require_write($wpdb->insert($table,[$column=>$id,'meta_key'=>$field,'meta_value'=>maybe_serialize($item)])); }
        wp_cache_delete($id,$type.'_meta');
    }
    public function allows_query($key, $sql) {
        global $wpdb; [$kind,$id,$field] = $this->split($key); $this->validate($key,null);
        $clean = preg_replace_callback("/'(?:[^'\\\\]|\\\\.)*'|`/s",function($match) { return $match[0]==='`' ? '' : $match[0]; },$sql);
        // Only statements addressing one covered row are admitted. Broad updates, custom
        // tables, account changes and schema/file operations need a dedicated adapter.
        if ($kind==='post' && ctype_digit($id)) {
            $values=$this->update_values($clean,$wpdb->posts,'ID',(int)$id,$this->post_fields());
            if ($values===null) { return false; }
            $this->validate_edit($key,$values); return true;
        }
        if ($kind==='term' && ctype_digit($id)) {
            $values=$this->update_values($clean,$wpdb->terms,'term_id',(int)$id,['name','slug','term_group']);
            if ($values===null) { return false; }
            $this->validate_edit($key,$values); return true;
        }
        if ($kind==='option') {
            return $this->update_query($clean,$wpdb->options,'option_name',"'".$id."'",['option_value','autoload']);
        }
        if (in_array($kind,['meta','acf','termmeta'],true)) {
            $table = $kind==='termmeta' ? $wpdb->termmeta : $wpdb->postmeta;
            $column = $kind==='termmeta' ? 'term_id' : 'post_id';
            $keys = $this->metadata_keys($kind,$field);
            if (!preg_match('/^\s*(UPDATE|DELETE FROM|INSERT INTO)\s+' . preg_quote($table,'/') . '\b/i',$clean)) { return false; }
            if (preg_match('/^\s*UPDATE\s+'.preg_quote($table,'/').'\s+SET\s+(.+)\s+WHERE\s+'.$column.'\s*=\s*\'?'.(int)$id.'\'?\s+AND\s+meta_key\s*=\s*\'([^\']+)\'\s*$/is',$clean,$m)) {
                $values=$this->assignment_values($m[1],['meta_value']);
                if (!in_array($m[2],$keys,true) || $values===null) { return false; }
                $this->authorize_browser_metadata($key,$kind,(int)$id,$field,$m[2],'edit',$values['meta_value']); return true;
            }
            // WP's metadata APIs update by meta_id. Resolve that immutable ID under the parent lock.
            if (preg_match('/WHERE\s+meta_id\s*(?:=\s*(\d+)|IN\s*\((\d+)\))\s*$/i',$clean,$m)) {
                $meta_id = (int)($m[1] ?: $m[2]);
                $row = $wpdb->get_row($wpdb->prepare("SELECT $column,meta_key FROM $table WHERE meta_id=%d",$meta_id),ARRAY_A);
                if (!$row || (int)$row[$column]!==(int)$id || !in_array($row['meta_key'],$keys,true)) { return false; }
                if (preg_match('/^\s*DELETE FROM\s+'.preg_quote($table,'/').'\s+WHERE\s+meta_id\s*(?:=\s*'.$meta_id.'|IN\s*\('.$meta_id.'\))\s*$/i',$clean)) {
                    $this->authorize_browser_metadata($key,$kind,(int)$id,$field,$row['meta_key'],'delete',null); return true;
                }
                $values=$this->update_values($clean,$table,'meta_id',$meta_id,['meta_value'],true);
                if ($values===null) { return false; }
                $this->authorize_browser_metadata($key,$kind,(int)$id,$field,$row['meta_key'],'edit',$values['meta_value']); return true;
            }
            if (preg_match('/^\s*INSERT INTO\s+\S+\s*\(\s*' . $column . '\s*,\s*meta_key\s*,\s*meta_value\s*\)\s*VALUES\s*\(\s*' . (int)$id . '\s*,\s*\'([^\']+)\'\s*,\s*(' . $this->literal() . ')\s*\)\s*$/is',$clean,$m)) {
                if (!in_array($m[1],$keys,true)) { return false; }
                $this->authorize_browser_metadata($key,$kind,(int)$id,$field,$m[1],'add',$this->literal_value($m[2])); return true;
            }
        }
        return false;
    }
    private function authorize_browser_metadata($key,$kind,$id,$field,$name,$operation,$value) {
        $this->authorize_read($key);
        $values=$operation==='delete' ? [] : [maybe_unserialize($value)];
        $reference=$kind==='acf' && $name==='_'.$field ? $field : ($kind==='meta' ? $this->acf_reference_field($id,$name,$values) : null);
        $this->authorize_metadata($kind==='termmeta' ? 'term' : 'post',$id,$name,['edit',$operation],$reference!==null,true);
        if ($reference!==null) { $this->validate_acf_reference($id,$reference,$values); }
    }
    private function literal() { return "(?:NULL|[+-]?[0-9]+(?:\\.[0-9]+)?|'(?:[^'\\\\]|\\\\.)*')"; }
    private function update_query($sql,$table,$column,$value,$fields,$allow_in=false) {
        return $this->update_values($sql,$table,$column,$value,$fields,$allow_in)!==null;
    }
    private function update_values($sql,$table,$column,$value,$fields,$allow_in=false) {
        $where = $allow_in ? '(?:=\\s*'.preg_quote((string)$value,'/').'|IN\\s*\\('.preg_quote((string)$value,'/').'\\))' : '=\\s*'.preg_quote((string)$value,'/');
        if (!preg_match('/^\\s*UPDATE\\s+'.preg_quote($table,'/').'\\s+SET\\s+(.+)\\s+WHERE\\s+'.preg_quote($column,'/').'\\s*'.$where.'\\s*$/is',$sql,$m)) { return null; }
        return $this->assignment_values($m[1],$fields);
    }
    private function assignment_values($set,$fields) {
        $assignment = '\\s*[a-zA-Z0-9_]+\\s*=\\s*'.$this->literal().'\\s*';
        if (!preg_match('/^'.$assignment.'(?:,'.$assignment.')*$/is',$set)) { return null; }
        preg_match_all('/(?:^|,)\\s*([a-zA-Z0-9_]+)\\s*=\\s*('.$this->literal().')/is',$set,$matches,PREG_SET_ORDER);
        $values=[];
        foreach ($matches as $match) {
            if (!in_array($match[1],$fields,true) || array_key_exists($match[1],$values)) { return null; }
            $values[$match[1]]=$this->literal_value($match[2]);
        }
        return $values;
    }
    private function literal_value($value) {
        if (strcasecmp($value,'NULL')===0) { return null; }
        if ($value[0]!=="'") { return $value; }
        return preg_replace_callback('/\\\\(.)/s',function($match) {
            $escapes=['0'=>"\0",'b'=>"\x08",'n'=>"\n",'r'=>"\r",'t'=>"\t",'Z'=>"\x1a",'%'=>'\\%','_'=>'\\_'];
            return $escapes[$match[1]]??$match[1];
        },substr($value,1,-1));
    }
}
