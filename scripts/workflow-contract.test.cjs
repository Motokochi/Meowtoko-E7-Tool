const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');

test('verification workflow is read-only, immutable, pinned, and bounded to source changes', () => {
  const workflow = fs.readFileSync(path.join(root, '.github', 'workflows', 'verify.yml'), 'utf8');

  assert.match(workflow, /pull_request:/);
  assert.match(workflow, /push:\s*\r?\n\s*branches:\s*\[main]/);
  assert.match(workflow, /permissions:\s*\r?\n\s*contents: read/);
  assert.match(workflow, /runs-on: windows-2025/);
  assert.match(workflow, /python-version: '3\.12\.10'/);
  assert.match(workflow, /node-version: '24\.10\.0'/);
  assert.match(workflow, /pnpm@11\.9\.0/);
  assert.match(workflow, /cancel-in-progress: true/);
  assert.doesNotMatch(workflow, /\$\{\{\s*secrets\./);

  const actionUses = [...workflow.matchAll(/uses:\s*([^\s#]+)(?:\s+#.*)?$/gm)].map((match) => match[1]);
  assert.ok(actionUses.length >= 3);
  for (const action of actionUses) {
    assert.match(action, /^[^@]+@[0-9a-f]{40}$/);
  }
});

test('release workflow is tag-only, non-cancelling, draft-first, and publishes only after audits', () => {
  const workflow = fs.readFileSync(path.join(root, '.github', 'workflows', 'release.yml'), 'utf8');

  assert.match(workflow, /tags:\s*\r?\n\s*- 'v\*\.\*\.\*'/);
  assert.match(workflow, /permissions:\s*\r?\n\s*contents: write/);
  assert.match(workflow, /runs-on: windows-2025/);
  assert.match(workflow, /cancel-in-progress: false/);
  assert.match(workflow, /gh release create .*--draft/);
  assert.match(workflow, /pnpm --dir desktop make/);
  assert.match(workflow, /verify_frozen_backend\.py/);
  assert.match(workflow, /smoke:package/);
  assert.match(workflow, /smoke:single-instance/);
  assert.match(workflow, /Meowtoko\.E7\.Tool-win32-x64-/);
  assert.doesNotMatch(workflow, /\$releaseRoot\/Meowtoko E7 Tool-win32-x64-/);
  assert.match(workflow, /gh release edit .*--draft=false --latest/);
  assert.match(workflow, /if: failure\(\)/);
  assert.doesNotMatch(workflow, /\$\{\{\s*secrets\./);

  const draft = workflow.indexOf('Create unpublished release draft');
  const build = workflow.indexOf('Build and audit installer');
  const publish = workflow.indexOf('Publish verified release');
  assert.ok(draft >= 0 && draft < build && build < publish);

  const actionUses = [...workflow.matchAll(/uses:\s*([^\s#]+)(?:\s+#.*)?$/gm)].map((match) => match[1]);
  assert.ok(actionUses.length >= 4);
  for (const action of actionUses) {
    assert.match(action, /^[^@]+@[0-9a-f]{40}$/);
  }
});
