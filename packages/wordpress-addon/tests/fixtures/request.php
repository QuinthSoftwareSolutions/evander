<?php
$args=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);
$curl=curl_init('http://127.0.0.1'.$args['path']);
$headers=['Host: design.example.test'];
foreach (($args['headers']??[]) as $key=>$value) { $headers[]=$key.': '.$value; }
curl_setopt_array($curl,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_TIMEOUT=>20,CURLOPT_CUSTOMREQUEST=>$args['method'],CURLOPT_HTTPHEADER=>$headers]);
if (!empty($args['auth'])) { curl_setopt($curl,CURLOPT_USERPWD,implode(':',$args['auth'])); }
if (isset($args['body'])) { curl_setopt($curl,CURLOPT_POSTFIELDS,$args['body']); }
$body=curl_exec($curl); $status=curl_getinfo($curl,CURLINFO_RESPONSE_CODE);
if ($body===false) { throw new RuntimeException('Fixture HTTP call failed'); }
echo json_encode(['status'=>$status,'body'=>$body]);
