// Reuse Node startup, but give every request a new Plugin API and JS global context.
import fs from 'node:fs';
import readline from 'node:readline';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('./figma_canvas_host.js', import.meta.url), 'utf8');
const host = new vm.Script(`(async () => { ${source} })()`, {filename: 'figma_canvas_host.js'});
async function execute(input) {
  return host.runInNewContext({input, structuredClone}, {timeout: 5000});
}

if (process.argv.includes('--worker')) {
  const requests = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
  for await (const request of requests) {
    process.stdout.write(JSON.stringify(await execute(JSON.parse(request))) + '\n');
  }
} else {
  process.stdout.write(JSON.stringify(await execute(JSON.parse(fs.readFileSync(0, 'utf8')))));
}
