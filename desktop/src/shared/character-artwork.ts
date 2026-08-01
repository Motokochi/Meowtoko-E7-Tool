export const CHARACTER_ARTWORK_SCHEME = 'e7-character';
export const CHARACTER_ARTWORK_HOST = 'artwork';
export const CHARACTER_ARTWORK_PATH = '/image';

export type CharacterArtworkVariant = 'pose' | 'face_l' | 'face_s' | 'face_su';

export function characterArtworkUrl(
  characterName: string,
  variant: CharacterArtworkVariant = 'pose',
): string {
  const url = new URL(`${CHARACTER_ARTWORK_SCHEME}://${CHARACTER_ARTWORK_HOST}${CHARACTER_ARTWORK_PATH}`);
  url.searchParams.set('name', characterName);
  url.searchParams.set('variant', variant);
  return url.toString();
}
