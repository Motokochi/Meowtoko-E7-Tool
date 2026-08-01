import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { APP_USER_MODEL_ID, handleSquirrelLifecycle, retireLegacyShortcuts } from './squirrel-lifecycle';

test('uses the stable Squirrel application identity', () => {
  assert.equal(APP_USER_MODEL_ID, 'com.squirrel.E7Hub.E7Hub');
});

test('retires only the legacy E7 Hub shortcuts', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'e7-shortcut-migration-'));
  try {
    const appDataPath = path.join(root, 'Roaming');
    const desktopPath = path.join(root, 'Desktop');
    const legacyDirectory = path.join(
      appDataPath,
      'Microsoft',
      'Windows',
      'Start Menu',
      'Programs',
      'E7 Hub contributors',
    );
    const currentDirectory = path.join(
      appDataPath,
      'Microsoft',
      'Windows',
      'Start Menu',
      'Programs',
      'Meowtoko E7 Tool contributors',
    );
    mkdirSync(legacyDirectory, { recursive: true });
    mkdirSync(currentDirectory, { recursive: true });
    mkdirSync(desktopPath, { recursive: true });
    writeFileSync(path.join(legacyDirectory, 'E7 Hub.lnk'), 'legacy');
    writeFileSync(path.join(desktopPath, 'E7 Hub.lnk'), 'legacy');
    writeFileSync(path.join(currentDirectory, 'Meowtoko E7 Tool.lnk'), 'current');

    retireLegacyShortcuts(appDataPath, desktopPath);

    assert.equal(existsSync(path.join(legacyDirectory, 'E7 Hub.lnk')), false);
    assert.equal(existsSync(path.join(desktopPath, 'E7 Hub.lnk')), false);
    assert.equal(existsSync(legacyDirectory), false);
    assert.equal(existsSync(path.join(currentDirectory, 'Meowtoko E7 Tool.lnk')), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

for (const event of ['--squirrel-install', '--squirrel-updated'] as const) {
  test(`${event} creates the E7Hub shortcut and quits`, () => {
    const calls: unknown[][] = [];
    let unrefCalled = false;
    let quitCalled = false;
    const handled = handleSquirrelLifecycle({
      argv: ['E7Hub.exe', event],
      execPath: String.raw`C:\Users\tester\AppData\Local\E7Hub\app-0.1.0\E7Hub.exe`,
      platform: 'win32',
      spawnDetached: (command, args, options) => {
        calls.push([command, args, options]);
        return { unref: () => { unrefCalled = true; } };
      },
      scheduleQuit: (callback, delayMs) => {
        assert.equal(delayMs, 1_000);
        callback();
      },
      quit: () => { quitCalled = true; },
    });

    assert.equal(handled, true);
    assert.deepEqual(calls, [[
      path.resolve(String.raw`C:\Users\tester\AppData\Local\E7Hub`, 'Update.exe'),
      ['--createShortcut', 'E7Hub.exe'],
      { detached: true, stdio: 'ignore', windowsHide: true },
    ]]);
    assert.equal(unrefCalled, true);
    assert.equal(quitCalled, true);
  });
}

test('uninstall removes shortcuts without deleting user data', () => {
  let commandArguments: readonly string[] = [];
  const handled = handleSquirrelLifecycle({
    argv: ['E7Hub.exe', '--squirrel-uninstall'],
    execPath: String.raw`C:\Users\tester\AppData\Local\E7Hub\app-0.1.0\E7Hub.exe`,
    platform: 'win32',
    spawnDetached: (_command, args) => {
      commandArguments = args;
      return { unref: () => undefined };
    },
    scheduleQuit: (callback) => callback(),
  });

  assert.equal(handled, true);
  assert.deepEqual(commandArguments, ['--removeShortcut', 'E7Hub.exe']);
});

test('obsolete and ordinary launches avoid shortcut subprocesses', () => {
  let spawned = false;
  let quitCalled = false;
  assert.equal(handleSquirrelLifecycle({
    argv: ['E7Hub.exe', '--squirrel-obsolete'],
    execPath: String.raw`C:\app\E7Hub.exe`,
    platform: 'win32',
    spawnDetached: () => {
      spawned = true;
      return { unref: () => undefined };
    },
    scheduleQuit: (callback, delayMs) => {
      assert.equal(delayMs, 0);
      callback();
    },
    quit: () => { quitCalled = true; },
  }), true);
  assert.equal(spawned, false);
  assert.equal(quitCalled, true);

  assert.equal(handleSquirrelLifecycle({
    argv: ['E7Hub.exe'],
    platform: 'win32',
  }), false);
});

test('maintenance still quits when Update.exe cannot be started', () => {
  let quitCalled = false;
  assert.equal(handleSquirrelLifecycle({
    argv: ['E7Hub.exe', '--squirrel-install'],
    execPath: String.raw`C:\app-0.1.0\E7Hub.exe`,
    platform: 'win32',
    spawnDetached: () => { throw new Error('missing Update.exe'); },
    scheduleQuit: (callback) => callback(),
    quit: () => { quitCalled = true; },
  }), true);
  assert.equal(quitCalled, true);
});
