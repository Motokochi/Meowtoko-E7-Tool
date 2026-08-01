import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';

import {
  CharacterArtworkResolver,
  resolveCharacterArtworkRoot,
} from './character-artwork';
import { characterArtworkUrl } from './shared/character-artwork';

const ROOT = path.resolve('C:', 'E7', 'assets', 'characters');
const MANIFEST = {
  schemaId: 'e7hub.e7codex-character-assets',
  characters: [{
    name: "Archdemon's Shadow",
    files: {
      pose: {
        status: 'available',
        path: "Archdemon's Shadow/pose.webp",
      },
      face_l: {
        status: 'available',
        path: "Archdemon's Shadow/face_l.webp",
      },
      face_s: {
        status: 'missing',
        path: "Archdemon's Shadow/face_s.webp",
      },
      face_su: {
        status: 'missing',
        path: "Archdemon's Shadow/face_su.webp",
      },
    },
  }],
};

test('builds an encoded internal artwork URL and resolves only manifest-listed files', () => {
  const resolver = new CharacterArtworkResolver(ROOT, MANIFEST);
  const poseUrl = characterArtworkUrl("Archdemon's Shadow");
  assert.equal(
    resolver.resolve(poseUrl),
    path.join(ROOT, "Archdemon's Shadow", 'pose.webp'),
  );
  assert.equal(
    resolver.resolve(characterArtworkUrl("Archdemon's Shadow", 'face_l')),
    path.join(ROOT, "Archdemon's Shadow", 'face_l.webp'),
  );
  assert.equal(resolver.resolve(characterArtworkUrl("Archdemon's Shadow", 'face_s')), null);
  assert.equal(resolver.resolve(characterArtworkUrl('Unknown Hero')), null);
});

test('rejects malformed protocol requests and manifest path traversal', () => {
  const resolver = new CharacterArtworkResolver(ROOT, MANIFEST);
  assert.equal(resolver.resolve('https://example.com/image?name=Archdemon&variant=pose'), null);
  assert.equal(resolver.resolve('e7-character://artwork/image?name=Archdemon&variant=private'), null);
  assert.equal(resolver.resolve('e7-character://artwork/other?name=Archdemon&variant=pose'), null);
  assert.equal(resolver.resolve('e7-character://artwork/image?name=Archdemon&variant=pose&extra=1'), null);
  assert.throws(
    () => new CharacterArtworkResolver(ROOT, {
      schemaId: 'e7hub.e7codex-character-assets',
      characters: [{
        name: 'Unsafe',
        files: { pose: { status: 'available', path: '../private.png' } },
      }],
    }),
    /escapes its root/,
  );
});

test('uses repository assets during development and external resources when packaged', () => {
  assert.equal(
    resolveCharacterArtworkRoot({
      appPath: path.resolve('C:', 'E7', 'desktop'),
      isPackaged: false,
      resourcesPath: path.resolve('C:', 'E7', 'resources'),
    }),
    path.resolve('C:', 'E7', 'assets', 'characters'),
  );
  assert.equal(
    resolveCharacterArtworkRoot({
      appPath: path.resolve('C:', 'Program Files', 'Meowtoko E7 Tool', 'resources', 'app.asar'),
      isPackaged: true,
      resourcesPath: path.resolve('C:', 'Program Files', 'Meowtoko E7 Tool', 'resources'),
    }),
    path.resolve('C:', 'Program Files', 'Meowtoko E7 Tool', 'resources', 'characters'),
  );
});
