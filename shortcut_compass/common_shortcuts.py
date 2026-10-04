"""Curated, context-aware shortcuts for the HUD's resting state.

Call on Blender's main thread in the target editor's context override. Every
row represents one effective, enabled primary binding. Search fallbacks and
sequential modal controls belong to the other HUD states, never this list.
"""
from . import engine


MODIFIERS = ("ctrl", "shift", "alt", "oskey", "hyper")
SECTIONS = {
    "context": (10, "当前操作", "Context"),
    "file": (20, "文件", "File"),
    "edit": (30, "编辑", "Edit"),
    "view": (40, "视图", "View"),
    "animation": (50, "动画", "Animation"),
    "other": (60, "其他", "Other"),
}


def _section(row):
    operator = row.get("operator", "")
    action = row.get("action", "")
    category = row.get("category", "")
    if operator in {"wm.save_mainfile", "wm.save_as_mainfile", "wm.open_mainfile", "wm.read_homefile"}:
        return "file"
    if operator.startswith("ed.") or action in {"undo", "redo", "search"}:
        return "edit"
    if category == "视图":
        return "view"
    if category == "动画":
        return "animation"
    return "context" if category not in {"通用", ""} else "other"


def _primary_item(row, keymaps, items_by_map):
    name = row.get("keymap", "")
    if name not in items_by_map:
        try:
            keymap = keymaps.get(name)
        except (AttributeError, ReferenceError, RuntimeError):
            keymap = None
        items_by_map[name] = engine._items(engine._attr(keymap, "keymap_items"))
    for item in items_by_map[name]:
        if (engine._attr(item, "active", False)
                and engine._attr(item, "idname") == row.get("operator")
                and engine._properties_match(engine._attr(item, "properties"), row.get("properties", {}))
                and engine._shortcut(item) == row.get("primary_shortcut")):
            return item
    return None


def common_shortcuts(context, info=None, combinations_only=False):
    """Return all curated, applicable actions with their actual primary keys.

    Selection, editor mode and operator polls come from the existing engine.
    The optional combination filter examines actual required modifier flags
    and ``key_modifier``; labels containing slashes or plus signs are untouched.
    """
    info = info if info is not None else engine.gather_context(context)
    configs = engine._attr(engine._attr(context, "window_manager"), "keyconfigs")
    keymaps = engine._attr(engine._attr(configs, "user"), "keymaps")
    if keymaps is None:
        return []
    items_by_map, seen, rows = {}, set(), []
    for source in engine._recommend(context, info, limit=1000):
        action = source.get("action")
        if (not source.get("bound") or source.get("category") == "操作组合"
                or not source.get("primary_shortcut") or action in seen):
            continue
        item = _primary_item(source, keymaps, items_by_map)
        if item is None:
            continue
        any_modifiers = bool(engine._attr(item, "any", False))
        required = [] if any_modifiers else [name for name in MODIFIERS
            if engine._attr(item, name, False) is True or engine._attr(item, name, False) == 1]
        optional = [] if any_modifiers else [name for name in MODIFIERS
            if engine._attr(item, name, False) == -1]
        key_modifier = engine._attr(item, "key_modifier", "NONE")
        if combinations_only and not required and key_modifier == "NONE":
            continue
        row = dict(source)
        primary = source["primary_shortcut"]
        section = _section(row)
        order, zh, en = SECTIONS[section]
        row.update({
            "shortcut": primary, "suffix_shortcut": primary,
            "shortcuts": [primary], "key": engine._attr(item, "type", "NONE"),
            "event_type": engine._attr(item, "type", "NONE"),
            "event_value": engine._attr(item, "value", "PRESS"),
            "map_type": engine._attr(item, "map_type", "KEYBOARD"),
            "direction": engine._attr(item, "direction", "ANY"),
            "key_modifier": key_modifier, "required_modifiers": required,
            "optional_modifiers": optional, "any_modifiers": any_modifiers,
            "section": section, "section_order": order,
            "section_label": zh, "section_label_zh": zh, "section_label_en": en,
            # Common actions have curated labels, but matching these labels
            # alone never proves that two operator effects are equivalent.
            "merge_key": "", "merge_label_zh": "", "merge_label_en": "",
            "is_branch": False, "branch": False,
        })
        seen.add(action)
        rows.append(row)
    # Stable within each section: preserve the curated G / R / S order.
    rows.sort(key=lambda row: row["section_order"])
    return rows
