import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { OptimizerCenter } from './optimizer-center';
import { EMPTY_INVENTORY, IMPORT_RESULT } from './optimizer-inventory-fixtures';

test('keeps inventory import controls out of the Optimizer workspace', () => {
  const markup = renderToStaticMarkup(<OptimizerCenter inventory={EMPTY_INVENTORY} />);

  assert.match(markup, /optimizer-workbench-page/);
  assert.match(markup, /Select a character/);
  assert.match(markup, /Importer workspace before searching/);
  assert.doesNotMatch(markup, /Owned gear inventory|Select gear\.txt|Import another gear\.txt|Latest import/);
});

test('renders the compact optimizer configuration once inventory exists', () => {
  const render = (): string => renderToStaticMarkup(
    <OptimizerCenter inventory={IMPORT_RESULT.inventory} />,
  );
  const first = render();
  assert.equal(first, render());
  assert.equal((first.match(/optimizer-compact-workspace/g) ?? []).length, 1);
  assert.doesNotMatch(first, /optimizer-inventory-toolbar|gear\.txt|optimizer-stage-card/);
});
