"""Check the actual fetch wrapper against plain-text reverse-proxy responses."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_non_json_gateway_errors_keep_http_status_and_trace_context():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for this browser fetch-wrapper check")
    frontend = Path(__file__).resolve().parents[2] / "apps/demo-rag/frontend/app.js"
    program = r"""
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(process.argv[2], 'utf8');
const actual = source.slice(
  source.indexOf('async function api('), source.indexOf('\nfunction json('));
const checks = [
  [413, 'Payload Too Large', null, /server rejected.*HTTP 413/],
  [502, 'Bad Gateway', null, /gateway could not reach.*HTTP 502/],
  [500, 'Internal Server Error', 'a'.repeat(32), /HTTP 500.*Trace a{32}/],
];
for (const [status, body, trace, expected] of checks) {
  const context = vm.createContext({fetch: async () => new Response(body, {
    status, headers: trace ? {'X-Trace-ID': trace} : {},
  })});
  vm.runInContext(actual, context);
  await assert.rejects(context.api('/collections/example/upload'), expected);
}
console.log('Non-JSON proxy errors: status and trace preserved.');
"""
    result = subprocess.run(  # noqa: S603 - fixed Node runner and repository source path
        [node, "--input-type=module", "-", str(frontend)],
        input=program,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
