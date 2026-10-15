// Build a private Codex configuration from the session's existing model route.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'smol-toml';

const install = path.dirname(fileURLToPath(import.meta.url));
const root = process.argv[2];
const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.resolve('@openai/codex-security'))));
const pluginRoot = path.join(packageRoot, '_bundled_plugin');
if (!fs.existsSync(path.join(pluginRoot, 'scripts', 'validate_scan_contract.py'))) {
  throw new Error('Installed Codex Security package is missing its canonical validator');
}
const originalHome = process.env.CODEX_HOME || '/home/agent/.config/evander/codex';
const original = parse(fs.readFileSync(path.join(originalHome, 'config.toml'), 'utf8'));
const codexHome = path.join(root, 'codex-home');
const state = path.join(root, 'state');
const reports = path.join(root, 'reports');
for (const directory of [codexHome, state, reports]) fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
const environment = {
  CODEX_HOME: codexHome,
  CODEX_CLI_PATH: path.join(install, 'codex'),
  CODEX_SECURITY_SURFACE: 'cli',
  CODEX_SECURITY_SCAN_ROOT: reports,
  CODEX_SECURITY_STATE_DIR: state,
};
const config = {
  model: original.model || 'gpt-6-sol',
  model_reasoning_effort: original.model_reasoning_effort || 'high',
  model_provider: original.model_provider || 'openai',
  model_providers: original.model_providers || {},
  ...(process.env.EVANDER_CODEX_BROKER_SOCKET ? { forced_login_method: 'chatgpt', model_provider: 'openai', model_providers: {} } : {}),
  approval_policy: 'never',
  allow_login_shell: false,
  projects: { [root]: { trust_level: 'trusted' } },
  default_permissions: 'codex_security_scan',
  permissions: { codex_security_scan: { filesystem: { ':root': 'read', ':workspace_roots': 'write', [root]: 'write' } } },
  features: { goals: true },
  mcp_servers: { 'codex-security': {
    command: process.execPath, args: [path.join(pluginRoot, 'mcp', 'server.mjs')], cwd: pluginRoot,
    env: environment,
    env_vars: ['OPENAI_API_KEY', 'CODEX_API_KEY', 'EVANDER_CODEX_BROKER_SOCKET', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY'],
    startup_timeout_sec: 120, tool_timeout_sec: 345600,
  } },
};
fs.mkdirSync(path.join(codexHome, 'skills'), { recursive: true, mode: 0o700 });
for (const skill of fs.readdirSync(path.join(pluginRoot, 'skills'))) {
  const destination = path.join(codexHome, 'skills', skill);
  if (!fs.existsSync(destination)) fs.symlinkSync(path.join(pluginRoot, 'skills', skill), destination);
}
fs.writeFileSync(path.join(codexHome, 'config.toml'), stringify(config), { mode: 0o600 });
const request = JSON.parse(fs.readFileSync(path.join(root, 'request.json'), 'utf8'));
fs.mkdirSync(path.join(codexHome, 'codex-security'), { recursive: true, mode: 0o700 });
fs.writeFileSync(path.join(codexHome, 'codex-security', 'config.toml'), stringify({
  deep_scan: { max_time_hours: Math.max(0.001, Math.min(96, (request.timeout_seconds - 30) / 3600)) },
}), { mode: 0o600 });
process.stdout.write(JSON.stringify({ plugin_root: pluginRoot, environment }));
