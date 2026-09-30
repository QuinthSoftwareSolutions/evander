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
        if ($kind === 'acf' && !function_exists('get_field_object')) { throw new RuntimeException('ACF adapter unavailable',400); }
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
        if (in_array($kind,['meta','termmeta','acf'],true) && ($field === '_wp_attached_file' || preg_match('/password|secret|token|api_key/i',$field))) { throw new RuntimeException('Protected metadata',400); }
    }
    public function validate_edit($key, $value) {
        [$kind] = $this->split($key);
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
            return ['value'=>get_post_meta((int)$id,$field,true), 'reference'=>get_post_meta((int)$id,'_'.$field,true),
                    'exists'=>metadata_exists('post',(int)$id,$field)];
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
                if ($this->read($key) === null) { $this->require_write($wpdb->insert($wpdb->posts,array_merge(['ID'=>(int)$id],$value))); }
                else { $this->require_write($wpdb->update($wpdb->posts,$value,['ID'=>(int)$id])); }
                clean_post_cache((int)$id);
            }
            else {
                $value = array_merge(['post_author'=>get_current_user_id(),'post_date'=>current_time('mysql'),
                    'post_date_gmt'=>current_time('mysql',true),'post_status'=>'draft','post_type'=>'page'], $value);
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
            $this->write_metadata('post',(int)$id,'_'.$field,$exists ? [$value['reference']] : []);
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
        global $wpdb; [$kind,$id,$field] = $this->split($key);
        $clean = str_replace('`','',$sql);
        // Only statements addressing one covered row are admitted. Broad updates, custom
        // tables, account changes and schema/file operations need a dedicated adapter.
        if ($kind==='post' && ctype_digit($id)) {
            return $this->update_query($clean,$wpdb->posts,'ID',(int)$id,$this->post_fields());
        }
        if ($kind==='term' && ctype_digit($id)) {
            return $this->update_query($clean,$wpdb->terms,'term_id',(int)$id,['name','slug','term_group']);
        }
        if ($kind==='option') {
            return $this->update_query($clean,$wpdb->options,'option_name',"'".$id."'",['option_value','autoload']);
        }
        if (in_array($kind,['meta','acf','termmeta'],true)) {
            $table = $kind==='termmeta' ? $wpdb->termmeta : $wpdb->postmeta;
            $column = $kind==='termmeta' ? 'term_id' : 'post_id';
            $keys = $kind==='acf' ? [$field,'_'.$field] : [$field];
            if (!preg_match('/^\s*(UPDATE|DELETE FROM|INSERT INTO)\s+' . preg_quote($table,'/') . '\b/i',$clean)) { return false; }
            if (preg_match('/^\s*UPDATE\s+'.preg_quote($table,'/').'\s+SET\s+(.+)\s+WHERE\s+'.$column.'\s*=\s*\'?'.(int)$id.'\'?\s+AND\s+meta_key\s*=\s*\'([^\']+)\'\s*$/is',$clean,$m)) {
                return in_array($m[2],$keys,true) && $this->assignments($m[1],['meta_value']);
            }
            // WP's metadata APIs update by meta_id. Resolve that immutable ID under the parent lock.
            if (preg_match('/WHERE\s+meta_id\s*(?:=\s*(\d+)|IN\s*\((\d+)\))\s*$/i',$clean,$m)) {
                $meta_id = (int)($m[1] ?: $m[2]);
                $row = $wpdb->get_row($wpdb->prepare("SELECT $column,meta_key FROM $table WHERE meta_id=%d",$meta_id),ARRAY_A);
                return $row && (int)$row[$column]===(int)$id && in_array($row['meta_key'],$keys,true) &&
                    (preg_match('/^\s*DELETE FROM\b/i',$clean) || $this->update_query($clean,$table,'meta_id',$meta_id,['meta_value'],true));
            }
            if (preg_match('/^\s*INSERT INTO\s+\S+\s*\(\s*' . $column . '\s*,\s*meta_key\s*,\s*meta_value\s*\)\s*VALUES\s*\(\s*' . (int)$id . '\s*,\s*\'([^\']+)\'\s*,\s*' . $this->literal() . '\s*\)\s*$/is',$clean,$m)) { return in_array($m[1],$keys,true); }
        }
        return false;
    }
    private function literal() { return "(?:NULL|[+-]?[0-9]+(?:\\.[0-9]+)?|'(?:[^'\\\\]|\\\\.)*')"; }
    private function update_query($sql,$table,$column,$value,$fields,$allow_in=false) {
        $where = $allow_in ? '(?:=\\s*'.preg_quote((string)$value,'/').'|IN\\s*\\('.preg_quote((string)$value,'/').'\\))' : '=\\s*'.preg_quote((string)$value,'/');
        if (!preg_match('/^\\s*UPDATE\\s+'.preg_quote($table,'/').'\\s+SET\\s+(.+)\\s+WHERE\\s+'.preg_quote($column,'/').'\\s*'.$where.'\\s*$/is',$sql,$m)) { return false; }
        return $this->assignments($m[1],$fields);
    }
    private function assignments($set,$fields) {
        $assignment = '\\s*[a-zA-Z0-9_]+\\s*=\\s*'.$this->literal().'\\s*';
        if (!preg_match('/^'.$assignment.'(?:,'.$assignment.')*$/is',$set)) { return false; }
        $names = preg_replace("/'(?:[^'\\\\]|\\\\.)*'/s", "''", $set);
        preg_match_all('/(?:^|,)\\s*([a-zA-Z0-9_]+)\\s*=/',$names,$columns);
        return !array_diff($columns[1],$fields);
    }
}
