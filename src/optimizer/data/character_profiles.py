"""Deterministic, immutable selection of source-backed hero base profiles."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from src.optimizer.data.character_repository import (
    CharacterHeroRecord,
    CharacterRepository,
    load_bundled_character_repository,
)
from src.optimizer.data.schema_common import required_text
from src.optimizer.domain import HeroBaseProfile


DEFAULT_CHARACTER_PROFILE_LEVEL = 60
DEFAULT_CHARACTER_PROFILE_STARS = 6
DEFAULT_CHARACTER_PROFILE_LABEL = "Level 60 / 6 star / fully awakened"


class CharacterProfileSelectionError(ValueError):
    """An actionable profile-index or explicit-selection failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = required_text(code, "Character profile error code")
        self.path = required_text(path, "Character profile error path")
        self.message = required_text(message, "Character profile error message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


def _is_default_profile(profile: HeroBaseProfile) -> bool:
    return (
        profile.level == DEFAULT_CHARACTER_PROFILE_LEVEL
        and profile.stars == DEFAULT_CHARACTER_PROFILE_STARS
        and profile.label == DEFAULT_CHARACTER_PROFILE_LABEL
    )


def _profile_order(profile: HeroBaseProfile) -> tuple[int, int, str, str]:
    return (profile.level, profile.stars, profile.label.casefold(), profile.profile_id)


@dataclass(frozen=True, slots=True)
class CharacterProfileSelection:
    """A canonical hero/profile pair before any modifier is applied."""

    hero: CharacterHeroRecord
    profile: HeroBaseProfile

    def __post_init__(self) -> None:
        if not isinstance(self.hero, CharacterHeroRecord):
            raise CharacterProfileSelectionError(
                "invalid-hero-record",
                "selection.hero",
                "Expected an immutable CharacterHeroRecord.",
            )
        if not isinstance(self.profile, HeroBaseProfile):
            raise CharacterProfileSelectionError(
                "invalid-profile-record",
                "selection.profile",
                "Expected an immutable HeroBaseProfile.",
            )
        if self.profile not in self.hero.base_profiles:
            raise CharacterProfileSelectionError(
                "profile-hero-mismatch",
                "selection.profile.profileId",
                f"Profile {self.profile.profile_id!r} does not belong to hero {self.hero_id!r}.",
            )

    @property
    def hero_id(self) -> str:
        return self.hero.hero_id

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def is_default_for_new_selection(self) -> bool:
        return _is_default_profile(self.profile)


class CharacterProfileSelector:
    """Validated profile options with separate new-default and explicit paths."""

    __slots__ = (
        "_default_by_hero_id",
        "_heroes",
        "_heroes_by_id",
        "_profile_owner_by_id",
        "_profiles_by_hero_id",
        "_sealed",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("CharacterProfileSelector is immutable after construction.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        repository_or_heroes: CharacterRepository | Iterable[CharacterHeroRecord],
    ) -> None:
        if isinstance(repository_or_heroes, CharacterRepository):
            heroes = repository_or_heroes.heroes
        else:
            if isinstance(repository_or_heroes, (str, bytes, bytearray)):
                raise CharacterProfileSelectionError(
                    "invalid-hero-records",
                    "heroes",
                    "Expected a character repository or iterable of character records.",
                )
            try:
                heroes = tuple(repository_or_heroes)
            except TypeError:
                raise CharacterProfileSelectionError(
                    "invalid-hero-records",
                    "heroes",
                    "Expected a character repository or iterable of character records.",
                ) from None

        if not heroes:
            raise CharacterProfileSelectionError(
                "empty-hero-records",
                "heroes",
                "At least one character record is required.",
            )
        if not all(isinstance(hero, CharacterHeroRecord) for hero in heroes):
            raise CharacterProfileSelectionError(
                "invalid-hero-records",
                "heroes",
                "Every entry must be an immutable CharacterHeroRecord.",
            )

        ordered_heroes = tuple(sorted(heroes, key=lambda hero: hero.hero_id))
        heroes_by_id: dict[str, CharacterHeroRecord] = {}
        profiles_by_hero_id: dict[str, tuple[HeroBaseProfile, ...]] = {}
        profile_owner_by_id: dict[str, str] = {}
        default_by_hero_id: dict[str, HeroBaseProfile] = {}

        for hero in ordered_heroes:
            folded_hero_id = hero.hero_id.casefold()
            if folded_hero_id in heroes_by_id:
                raise CharacterProfileSelectionError(
                    "duplicate-hero-id",
                    f"heroes[{hero.hero_id!r}]",
                    "Stable hero IDs collide case-insensitively.",
                )
            heroes_by_id[folded_hero_id] = hero

            profiles = tuple(sorted(hero.base_profiles, key=_profile_order))
            if not profiles:
                raise CharacterProfileSelectionError(
                    "missing-profiles",
                    f"heroes[{hero.hero_id!r}].baseProfiles",
                    "The hero has no selectable base profiles.",
                )
            for profile in profiles:
                owner = profile_owner_by_id.get(profile.profile_id)
                if owner is not None:
                    raise CharacterProfileSelectionError(
                        "duplicate-profile-id",
                        f"profiles[{profile.profile_id!r}]",
                        f"The stable profile ID is claimed by heroes {owner!r} and {hero.hero_id!r}.",
                    )
                profile_owner_by_id[profile.profile_id] = hero.hero_id

            defaults = tuple(profile for profile in profiles if _is_default_profile(profile))
            if not defaults:
                raise CharacterProfileSelectionError(
                    "default-profile-missing",
                    f"heroes[{hero.hero_id!r}].baseProfiles",
                    "Expected one level-60, six-star, fully-awakened default profile.",
                )
            if len(defaults) > 1:
                profile_ids = ", ".join(profile.profile_id for profile in defaults)
                raise CharacterProfileSelectionError(
                    "default-profile-ambiguous",
                    f"heroes[{hero.hero_id!r}].baseProfiles",
                    f"Multiple profiles satisfy the new-selection default: {profile_ids}.",
                )
            profiles_by_hero_id[folded_hero_id] = profiles
            default_by_hero_id[folded_hero_id] = defaults[0]

        self._heroes = ordered_heroes
        self._heroes_by_id = MappingProxyType(heroes_by_id)
        self._profiles_by_hero_id = MappingProxyType(profiles_by_hero_id)
        self._profile_owner_by_id = MappingProxyType(profile_owner_by_id)
        self._default_by_hero_id = MappingProxyType(default_by_hero_id)
        self._sealed = True

    def __len__(self) -> int:
        return len(self._heroes)

    @property
    def heroes(self) -> tuple[CharacterHeroRecord, ...]:
        return self._heroes

    @property
    def profile_count(self) -> int:
        return len(self._profile_owner_by_id)

    def _hero(self, hero_id: object) -> tuple[str, CharacterHeroRecord]:
        if not isinstance(hero_id, str) or not hero_id.strip():
            raise CharacterProfileSelectionError(
                "hero-not-found",
                "heroId",
                f"Stable hero ID was not found: {hero_id!r}.",
            )
        folded_hero_id = hero_id.strip().casefold()
        hero = self._heroes_by_id.get(folded_hero_id)
        if hero is None:
            raise CharacterProfileSelectionError(
                "hero-not-found",
                "heroId",
                f"Stable hero ID was not found: {hero_id!r}.",
            )
        return folded_hero_id, hero

    def profiles_for(self, hero_id: object) -> tuple[HeroBaseProfile, ...]:
        folded_hero_id, _hero = self._hero(hero_id)
        return self._profiles_by_hero_id[folded_hero_id]

    def create_default_selection(self, hero_id: object) -> CharacterProfileSelection:
        """Create a new hero selection using the explicit source-backed default."""

        folded_hero_id, hero = self._hero(hero_id)
        return CharacterProfileSelection(hero, self._default_by_hero_id[folded_hero_id])

    def select(self, hero_id: object, profile_id: object) -> CharacterProfileSelection:
        """Resolve an explicit profile ID; this path never applies a default."""

        folded_hero_id, hero = self._hero(hero_id)
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise CharacterProfileSelectionError(
                "profile-not-found",
                "profileId",
                f"Stable profile ID was not found: {profile_id!r}.",
            )
        selected_profile_id = profile_id.strip()
        owner = self._profile_owner_by_id.get(selected_profile_id)
        if owner is None:
            raise CharacterProfileSelectionError(
                "profile-not-found",
                "profileId",
                f"Stable profile ID was not found: {profile_id!r}.",
            )
        if owner != hero.hero_id:
            raise CharacterProfileSelectionError(
                "profile-hero-mismatch",
                "profileId",
                f"Profile {selected_profile_id!r} belongs to hero {owner!r}, not {hero.hero_id!r}.",
            )
        profile = next(
            item
            for item in self._profiles_by_hero_id[folded_hero_id]
            if item.profile_id == selected_profile_id
        )
        return CharacterProfileSelection(hero, profile)


def load_bundled_character_profile_selector() -> CharacterProfileSelector:
    return CharacterProfileSelector(load_bundled_character_repository())


__all__ = [
    "DEFAULT_CHARACTER_PROFILE_LABEL",
    "DEFAULT_CHARACTER_PROFILE_LEVEL",
    "DEFAULT_CHARACTER_PROFILE_STARS",
    "CharacterProfileSelection",
    "CharacterProfileSelectionError",
    "CharacterProfileSelector",
    "load_bundled_character_profile_selector",
]
