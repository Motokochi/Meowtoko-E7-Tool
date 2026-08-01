"""
Constants and static game data for Epic Seven.
"""

from src.optimizer.domain import (
    DISPLAY_SET_ORDER,
    GEAR_SLOT_ORDER,
    ITEM_STAT_DISPLAY_ORDER,
    gear_set_display_name,
    gear_slot_display_name,
    item_stat_display_name,
)


ALL_SLOTS = [gear_slot_display_name(slot) for slot in GEAR_SLOT_ORDER]
ALL_SETS = [gear_set_display_name(gear_set) for gear_set in DISPLAY_SET_ORDER]
ALL_STATS = [item_stat_display_name(stat) for stat in ITEM_STAT_DISPLAY_ORDER]

SLOT_MAIN_STATS = {
    "Weapon": ["Flat Attack"],
    "Helmet": ["Flat Health"],
    "Armor": ["Flat Defense"],
    "Necklace": [
        "Critical Hit Chance", "Critical Hit Damage", "Health", "Defense",
        "Attack", "Flat Attack", "Flat Health", "Flat Defense"
    ],
    "Ring": [
        "Effectiveness", "Effect Resistance", "Health", "Defense", "Attack",
        "Flat Attack", "Flat Health", "Flat Defense"
    ],
    "Boots": [
        "Speed", "Health", "Defense", "Attack", "Flat Attack",
        "Flat Health", "Flat Defense"
    ]
}

RESTRICTED_SUBSTATS = {
    "Weapon": ["Flat Defense", "Defense"],
    "Helmet": [],
    "Armor": ["Flat Attack", "Attack"],
    "Necklace": [],
    "Ring": [],
    "Boots": []
}

# Translation Dictionary for OCR -> UI base stat mapping
STAT_TRANSLATION = {
    "attack": "Attack",
    "health": "Health",
    "defense": "Defense",
    "critical hit chance": "Critical Hit Chance",
    "critical hit damage": "Critical Hit Damage",
    "effectiveness": "Effectiveness",
    "effect resistance": "Effect Resistance",
    "speed": "Speed",
}
