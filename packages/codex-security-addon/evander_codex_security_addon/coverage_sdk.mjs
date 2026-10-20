// One SDK-owned review. The control plane alone decides coverage and continuation.
import fs from 'node:fs';
import path from 'node:path';
import { CodexSecurity } from '@openai/codex-security';
import { parse } from 'smol-toml';

const root = process.argv[2];
const request = JSON.parse(fs.readFileSync(path.join(root, 'request.json'), 'utf8'));
const setup = JSON.parse(fs.readFileSync(path.join(root, 'sdk-setup.json'), 'utf8'));
const sdkVersion = JSON.parse(fs.readFileSync(path.join(path.dirname(setup.plugin_root), 'package.json'), 'utf8')).version;
if (sdkVersion !== request.sdk_version) throw new Error('Pinned SDK version differs from the coverage journal');
const original = parse(fs.readFileSync(path.join(root, 'codex-home/config.toml'), 'utf8'));
const work = request.coverage_task;
const config = {
  model: request.frozen_route.model,
  model_reasoning_effort: request.frozen_route.reasoning_effort,
  model_provider: original.model_provider,
  model_providers: original.model_providers,
  approval_policy: 'never', allow_login_shell: false,
  features: { goals: true, multi_agent_v2: { enabled: true, max_concurrent_threads_per_session: 3 } },
  ...(process.env.EVANDER_CODEX_BROKER_SOCKET ? { forced_login_method: 'chatgpt' } : {}),
};
const instructions = [
  'Perform the installed Standard security scan for the exact SDK-authorized scope at the pinned revision.',
  'This is a component of a larger host-owned tracked-file review. Review every assigned file, including documentation, generated and vendored text. Do not widen the finding scope. Supporting source may be read to trace controls.',
  'Do not claim complete coverage when assigned review remains deferred. Resolve finished checkpoint IDs explicitly and retain unfinished candidates.',
  'For every assigned binary or symlink, add a coverage surface labelled exactly "file:<repository-relative path>" with a justified disposition and notes. Unsupported or unavailable content remains needs_follow_up.',
];
if (work.kind === 'architecture') instructions.push('Establish the repository architecture and trust boundaries. Preserve a source-backed threat model and source anchors. The host will separately account for all files; report this pass honestly even when its source coverage is partial.');
if (work.kind === 'boundary') {
  instructions.push('Review cross-component trust boundaries using the supplied map and retained candidates. For EACH supplied outstanding item add a surface labelled "deferred:<key>" with its explicit outcome and source-backed notes; unfinished items remain needs_follow_up.');
}
const knowledge = path.join(root, 'review-context.json');
fs.writeFileSync(knowledge, JSON.stringify({ architecture: work.architecture, assignedInventory: work.assigned_entries, outstanding: work.deferred, priorFindings: work.prior_findings, acceptedFileReviews: work.accepted_file_reviews }), { mode: 0o600 });
const security = new CodexSecurity({ pluginPath: setup.plugin_root, pythonPath: '/usr/bin/python3', codexOverrides: config });
const stop = new AbortController();
let retryAfter = 900;
process.on('SIGTERM', () => stop.abort(new Error('Host stopped scoped review')));
try {
  const result = await security.run(path.join(root, 'checkout'), {
    mode: 'standard', target: work.kind === 'architecture' ? 'repository' : work.paths,
    auth: process.env.EVANDER_CODEX_BROKER_SOCKET ? 'chatgpt' : 'auto',
    outputDir: path.join(root, 'reports/review'), knowledgeBasePaths: [knowledge],
    scanPrompt: instructions.join('\n'), signal: stop.signal,
    onReconnect: (_attempt, _maximum, details) => { if (details?.retryAfterSeconds) retryAfter = details.retryAfterSeconds; },
  });
  const native = result.manifest.scan;
  const scope = work.kind === 'architecture' ? ['.'] : work.paths;
  if (native.target.kind !== 'git_revision' || native.target.revision !== request.snapshot_commit ||
      JSON.stringify([...native.scope.includePaths].sort()) !== JSON.stringify([...scope].sort()) ||
      (result.coverage.excludePaths || []).length) throw new Error('SDK result scope mismatch');
  const findings = result.findings.findings;
  const counts = Object.fromEntries(['critical', 'high', 'medium', 'low', 'informational'].map(k => [k, 0]));
  for (const f of findings) counts[f.severity.level]++;
  const dispositions = {}, resolutions = {};
  for (const surface of result.coverage.surfaces || []) {
    const disposition = surface.disposition;
    if (surface.label?.startsWith('file:') && surface.notes && ['reported', 'no_issue_found', 'not_applicable'].includes(disposition)) {
      dispositions[surface.label.slice(5)] = { disposition: disposition === 'not_applicable' ? 'not_applicable' : 'reviewed', reason: surface.notes };
    }
    if (surface.label?.startsWith('deferred:') && surface.notes) resolutions[surface.label.slice(9)] = { disposition, reason: surface.notes };
  }
  const model = native.threatModel;
  const modelText = JSON.stringify(model || {});
  const anchors = work.inventory_paths.filter(p => modelText.includes(p));
  const artifacts = Object.fromEntries(['report.md', 'scan-manifest.json', 'findings.json', 'coverage.json'].map(name => [name, path.posix.join('security-scans', request.scan_id, 'reports/review', name)]));
  const value = {
    status: result.coverage.completeness === 'complete' ? 'completed' : 'failed',
    coverage: result.coverage.completeness, from_commit: null, to_commit: request.snapshot_commit,
    provider_scan_id: native.id, findings: findings.map(f => ({ id: f.findingId, occurrence_id: f.occurrenceId, title: f.title, severity: f.severity.level, locations: f.locations })),
    severity_counts: counts, artifacts,
    coverage_unit: { kind: work.kind, paths: work.paths, revision: request.snapshot_commit, sdk_version: sdkVersion,
      coverage: result.coverage.completeness, exclusions: result.coverage.explicitExclusions || [],
      deferred: result.coverage.deferred || [], needs_follow_up: (result.coverage.surfaces || []).some(s => s.disposition === 'needs_follow_up'),
      dispositions, resolutions, architecture: model ? { threat_model: model, paths: anchors } : null },
  };
  fs.writeFileSync(path.join(root, 'sdk-result.json'), JSON.stringify(value), { mode: 0o600, flag: 'wx' });
} catch (error) {
  const text = String(error?.message || '');
  const category = /cyberPolicy|cybersecurity risk|forbidden|\b403\b/i.test(text) ? 'policy'
    : /unauthorized|authentication|\b401\b/i.test(text) ? 'authentication'
    : /quota|usage limit|rate.limit|\b429\b/i.test(text) ? 'quota' : 'execution';
  fs.writeFileSync(path.join(root, 'sdk-result.json'), JSON.stringify({ status: 'failed', coverage: 'partial', artifacts: {}, failure_category: category, retry_after_seconds: retryAfter, error: 'Scoped review stopped; private diagnostics retained' }), { mode: 0o600, flag: 'wx' });
  console.error(error);
} finally {
  await security.close();
}
