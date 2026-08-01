import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  CHARACTER_ARTWORK_HOST,
  CHARACTER_ARTWORK_PATH,
  CHARACTER_ARTWORK_SCHEME,
  type CharacterArtworkVariant,
} from './shared/character-artwork';

const VARIANTS = new Set<CharacterArtworkVariant>(['pose', 'face_l', 'face_s', 'face_su']);

interface CharacterArtworkFile {
  path?: unknown;
  status?: unknown;
}

interface CharacterArtworkRecord {
  files?: unknown;
  name?: unknown;
}

interface CharacterArtworkManifest {
  characters?: unknown;
  schemaId?: unknown;
}

export interface CharacterArtworkRootContext {
  appPath: string;
  isPackaged: boolean;
  resourcesPath: string;
}

export function resolveCharacterArtworkRoot(context: CharacterArtworkRootContext): string {
  return context.isPackaged
    ? path.join(context.resourcesPath, 'characters')
    : path.resolve(context.appPath, '..', 'assets', 'characters');
}

function safeManifestPath(root: string, relativePath: string): string | null {
  const normalizedRoot = path.resolve(root);
  const candidate = path.resolve(normalizedRoot, ...relativePath.split('/'));
  const prefix = `${normalizedRoot}${path.sep}`;
  return candidate.startsWith(prefix) ? candidate : null;
}

export class CharacterArtworkResolver {
  readonly #files: ReadonlyMap<string, string>;

  constructor(root: string, manifest: CharacterArtworkManifest) {
    if (manifest.schemaId !== 'e7hub.e7codex-character-assets' || !Array.isArray(manifest.characters)) {
      throw new Error('Character artwork manifest is invalid.');
    }

    const files = new Map<string, string>();
    for (const rawCharacter of manifest.characters) {
      const character = rawCharacter as CharacterArtworkRecord;
      if (typeof character.name !== 'string' || !character.name || typeof character.files !== 'object' || character.files === null) {
        continue;
      }
      for (const variant of VARIANTS) {
        const file = (character.files as Record<string, CharacterArtworkFile>)[variant];
        if (file?.status !== 'available' || typeof file.path !== 'string' || !file.path) continue;
        const absolutePath = safeManifestPath(root, file.path);
        if (!absolutePath) throw new Error(`Character artwork path escapes its root: ${file.path}`);
        files.set(`${character.name}\0${variant}`, absolutePath);
      }
    }
    this.#files = files;
  }

  static load(root: string): CharacterArtworkResolver {
    const manifest = JSON.parse(
      readFileSync(path.join(root, 'asset-manifest.json'), 'utf8'),
    ) as CharacterArtworkManifest;
    return new CharacterArtworkResolver(root, manifest);
  }

  resolve(requestUrl: string): string | null {
    let url: URL;
    try {
      url = new URL(requestUrl);
    } catch {
      return null;
    }
    if (
      url.protocol !== `${CHARACTER_ARTWORK_SCHEME}:`
      || url.hostname !== CHARACTER_ARTWORK_HOST
      || url.pathname !== CHARACTER_ARTWORK_PATH
      || url.username
      || url.password
      || url.port
      || url.hash
    ) {
      return null;
    }
    const keys = [...url.searchParams.keys()];
    if (keys.length !== 2 || !keys.includes('name') || !keys.includes('variant')) return null;
    const name = url.searchParams.get('name');
    const variant = url.searchParams.get('variant');
    if (!name || !variant || !VARIANTS.has(variant as CharacterArtworkVariant)) return null;
    return this.#files.get(`${name}\0${variant}`) ?? null;
  }
}
