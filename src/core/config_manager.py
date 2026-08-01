import json
import os

from src.core.settings_service import SettingsService, default_settings
from src.core.workspace_paths import DEFAULT_DEVELOPMENT_USER_DATA

USER_DATA_DIR = os.fspath(DEFAULT_DEVELOPMENT_USER_DATA)
DEBUG_DIR = os.path.join(USER_DATA_DIR, "debug_images")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "settings.json")
ARCHETYPES_FILE = os.path.join(USER_DATA_DIR, "archetypes.json")

def initialize_directories():
    """Ensure the development user-data and debug-image directories exist."""
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)


def get_default_settings():
    return default_settings()


def load_settings():
    """Load a normalized, migration-ready document without changing the file."""
    return SettingsService(SETTINGS_FILE).load().document


def _merge_defaults(loaded, defaults):
    for key, value in defaults.items():
        if key not in loaded:
            loaded[key] = value
        elif isinstance(value, dict) and isinstance(loaded[key], dict):
            _merge_defaults(loaded[key], value)
    return loaded

def save_settings(settings_dict):
    """Keep the legacy UI on the validated atomic settings writer."""
    try:
        SettingsService(SETTINGS_FILE).replace_legacy(settings_dict)
    except Exception as e:
        print(f"Failed to save settings: {e}")

def load_archetypes():
    """Loads the user's archetypes."""
    if not os.path.exists(ARCHETYPES_FILE):
        return []
    try:
        with open(ARCHETYPES_FILE, "r") as f:
            return json.load(f)
    except:
        return []
