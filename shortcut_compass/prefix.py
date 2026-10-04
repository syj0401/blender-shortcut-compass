"""Effective keymap bindings and held-key prefix completion.

Enumeration belongs on Blender's main thread, with the target editor's context
override. ``filter_bindings`` is pure: it never calls Blender or executes a
shortcut. Keymap precedence here is a useful conflict filter, not a promise
about dispatch while a separate modal operator owns the editor.
"""
import hashlib
import json

import bpy

from . import engine


MODIFIERS = ("ctrl", "shift", "alt", "oskey", "hyper")
MODIFIER_LABELS = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
    "oskey": "Win / Cmd", "hyper": "Hyper",
}
MODIFIER_EVENTS = {
    "LEFT_CTRL", "RIGHT_CTRL", "LEFT_SHIFT", "RIGHT_SHIFT",
    "LEFT_ALT", "RIGHT_ALT", "OSKEY", "HYPER",
}
MODIFIER_EVENT_NAMES = {
    "LEFT_CTRL": "ctrl", "RIGHT_CTRL": "ctrl", "LEFT_SHIFT": "shift", "RIGHT_SHIFT": "shift",
    "LEFT_ALT": "alt", "RIGHT_ALT": "alt", "OSKEY": "oskey", "HYPER": "hyper",
}
# Keep familiar completions visible on the first small HUD page. Every actual
# matching binding remains available; this only changes its presentation order.
COMPLETION_KEY_PRIORITY = {
    key: index for index, key in enumerate(("S", "Z", "C", "V", "X", "A", "F", "D", "R", "T", "B", "N", "O", "P", "E", "G", "I", "M", "U", "W", "Y", "H", "J", "K", "L", "Q"))
}


def _json_value(value, depth=0):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 4:
        return None
    if isinstance(value, (set, frozenset)):
        return sorted(_json_value(v, depth + 1) for v in value)
    if isinstance(value, (list, tuple)) or type(value).__name__ in {"bpy_prop_array", "IDPropertyArray"}:
        return [_json_value(v, depth + 1) for v in value]
    if hasattr(value, "keys"):
        return _properties(value, depth + 1)
    return None


def _properties(properties, depth=0):
    """Export explicit operator properties, including nested macro properties."""
    result = {}
    try:
        names = list(properties.keys())
    except (AttributeError, TypeError, ReferenceError, RuntimeError):
        return result
    for name in names:
        if name == "rna_type":
            continue
        try:
            result[str(name)] = _json_value(getattr(properties, name), depth)
        except (AttributeError, TypeError, ReferenceError, RuntimeError):
            continue
    return result


def _translate(label, translation_context="*"):
    try:
        return bpy.app.translations.pgettext_iface(label, translation_context)
    except (AttributeError, TypeError, RuntimeError):
        return label


def _operator(operator):
    try:
        namespace, name = operator.split(".", 1)
        return getattr(getattr(bpy.ops, namespace), name)
    except (AttributeError, ValueError, RuntimeError):
        return None


def _active_tool_keymap(context, info):
    tools = engine._attr(engine._attr(context, "workspace"), "tools")
    if not tools:
        return ""
    try:
        editor = info["editor"]
        if editor == "VIEW_3D":
            tool = tools.from_space_view3d_mode(info["mode"], create=False)
        elif editor == "IMAGE_EDITOR":
            mode = info.get("space_mode") or engine._attr(engine._attr(context, "space_data"), "mode", "VIEW")
            tool = tools.from_space_image_mode(mode, create=False)
        elif editor == "NODE_EDITOR" and hasattr(tools, "from_space_node"):
            tool = tools.from_space_node(info["ui_type"], create=False)
        else:
            tool = None
        keymap = engine._attr(tool, "keymap", "")
        if keymap:
            return keymap
        # WorkSpaceTool RNA in Blender 5.2 exposes idname but no keymap. Use
        # Blender's own registered Python ToolDef, as its tooltips do.
        tool_id = engine._attr(tool, "idname", "") or info.get("tool", {}).get("idname", "")
        if tool_id:
            from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
            cls = ToolSelectPanelHelper._tool_class_from_space_type(editor)
            if cls:
                definition, _index = cls._tool_get_by_id(context, tool_id)
                keymap = engine._attr(definition, "keymap")
                if keymap and isinstance(keymap[0], str):
                    return keymap[0]
        return ""
    except (AttributeError, ImportError, KeyError, TypeError, ValueError, RuntimeError, ReferenceError):
        return ""


def _context_path_label(context, path):
    """Name a context property without changing its value."""
    try:
        owner = context
        parts = path.split(".")
        for part in parts[:-1]:
            owner = getattr(owner, part)
        prop = owner.bl_rna.properties.get(parts[-1])
        return prop.name if prop else parts[-1].replace("_", " ").title()
    except (AttributeError, KeyError, TypeError, RuntimeError, ReferenceError):
        return path.split(".")[-1].replace("_", " ").title()


def _labels(context, item, operator, props, curated):
    """Use native RNA/menu names; only apply exact curated property labels."""
    name = engine._attr(item, "idname", "")
    try:
        rna = operator.get_rna_type()
        label = rna.name
        label_zh = _translate(label, engine._attr(rna, "translation_context", "Operator"))
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        label = name.replace(".", " · ").replace("_", " ").title()
        label_zh = _translate(label)

    if name in {"wm.call_menu", "wm.call_menu_pie", "wm.call_panel"}:
        cls = getattr(bpy.types, props.get("name", ""), None)
        if cls is not None:
            label = engine._attr(cls, "bl_label", label) or label
            label_zh = _translate(label, engine._attr(cls, "bl_translation_context", "*"))

    if name == "view3d.view_axis":
        direction = props.get("type")
        view_labels = {
            "FRONT": ("前视图", "Front View"), "BACK": ("后视图", "Back View"),
            "LEFT": ("左视图", "Left View"), "RIGHT": ("右视图", "Right View"),
            "TOP": ("顶视图", "Top View"), "BOTTOM": ("底视图", "Bottom View"),
        }
        if direction in view_labels:
            label_zh, label = view_labels[direction]
            if props.get("align_active"):
                label_zh += " · 对齐所选"
                label += " · Align Active"
            return label_zh, label
    if name == "object.subdivision_set" and isinstance(props.get("level"), int):
        level = props["level"]
        if props.get("relative", False):
            return "调整细分等级 {:+d}".format(level), "Change Subdivision Level {:+d}".format(level)
        return "细分等级 {}".format(level), "Subdivision Level {}".format(level)

    for action in curated:
        variants = ((action.operator, action.properties),) + action.alternatives
        if any(name == op and props == expected for op, expected in variants):
            return action.zh, action.en

    # Generic context operators otherwise all read "Toggle Context Boolean".
    # Keep the native operation name while identifying its actual property.
    path = props.get("data_path")
    if name.startswith("wm.context_") and isinstance(path, str) and path:
        target = _context_path_label(context, path)
        label += " · " + target
        label_zh += " · " + _translate(target)
        if "value" in props and isinstance(props["value"], (str, int, float)):
            value = str(props["value"])
            label += " = " + value
            label_zh += " = " + _translate(value)
    return label_zh, label


def _viable(context, item, operator, props):
    if operator is None:
        return False
    try:
        if not operator.poll():
            return False
    except (AttributeError, RuntimeError, ReferenceError, TypeError, ValueError):
        return False
    idname = engine._attr(item, "idname", "")
    if idname.startswith("wm.context_"):
        path = props.get("data_path", "")
        if path and not engine._path_exists(context, path):
            return False
    if idname == "wm.radial_control":
        path = props.get("data_path_primary", "")
        if path and not engine._path_exists(context, path):
            return False
    if idname in {"wm.call_menu", "wm.call_menu_pie", "wm.call_panel"}:
        cls = getattr(bpy.types, props.get("name", ""), None)
        if cls is None:
            return False
        poll = getattr(cls, "poll", None)
        if poll:
            try:
                if not poll(context):
                    return False
            except (AttributeError, RuntimeError, ReferenceError, TypeError, ValueError):
                return False
    return True


def enumerate_bindings(context):
    """Read active bindings for the current editor/mode from keyconfigs.user.

    This includes custom operators and key bindings, not just a curated list.
    Inactive bindings, unavailable operators and non-applicable context paths
    are omitted. Modal maps need a separate active-modal context and are not
    mixed into ordinary shortcut completion.
    """
    info = engine.gather_context(context)
    configs = engine._attr(engine._attr(context, "window_manager"), "keyconfigs")
    keymaps = engine._attr(engine._attr(configs, "user"), "keymaps")
    if keymaps is None:
        return []
    names = engine._keymap_names(info)
    if info["editor"] == "NLA_EDITOR":
        names = ["NLA Editor", "NLA Channels", "NLA Generic"] + names
    active_tool = _active_tool_keymap(context, info)
    if active_tool:
        names.insert(0, active_tool)
    names = list(dict.fromkeys(names))
    curated = engine._candidates(info)
    records = []
    seen_events = set()
    operators = {}
    for priority, name in enumerate(names):
        try:
            keymap = keymaps.get(name)
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if keymap is None or engine._attr(keymap, "is_modal", False):
            continue
        for item in engine._items(engine._attr(keymap, "keymap_items", ())):
            if not engine._attr(item, "active", False):
                continue
            key = engine._attr(item, "type", "NONE")
            if key in MODIFIER_EVENTS:
                continue
            shortcut = engine._shortcut(item)
            if not shortcut:
                continue
            map_type = engine._attr(item, "map_type", "KEYBOARD")
            modifiers = tuple(engine._attr(item, mod, 0) for mod in MODIFIERS)
            any_modifiers = bool(engine._attr(item, "any", False))
            key_modifier = engine._attr(item, "key_modifier", "NONE")
            event_value = engine._attr(item, "value", "PRESS")
            direction = engine._attr(item, "direction", "ANY") if event_value == "CLICK_DRAG" else "ANY"
            signature = (key, event_value, direction, map_type, any_modifiers, modifiers, key_modifier)
            if signature in seen_events:
                continue
            idname = engine._attr(item, "idname", "")
            if idname not in operators:
                operators[idname] = _operator(idname)
            operator = operators[idname]
            props = _properties(engine._attr(item, "properties"))
            if not _viable(context, item, operator, props):
                continue
            seen_events.add(signature)
            zh, en = _labels(context, item, operator, props, curated)
            required = [mod for mod, value in zip(MODIFIERS, modifiers) if value is True or value == 1]
            optional = [mod for mod, value in zip(MODIFIERS, modifiers) if value == -1]
            identity = json.dumps([name, idname, signature, props], ensure_ascii=False, sort_keys=True)
            uid = "binding_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
            records.append({
                "id": uid, "action": uid, "label_zh": zh, "label_en": en,
                "operator": idname, "properties": props,
                "keymap": name, "keymap_priority": priority, "map_type": map_type,
                "required_modifiers": required, "optional_modifiers": optional,
                "any_modifiers": any_modifiers, "key_modifier": key_modifier,
                "key": key, "event_value": event_value, "event_direction": direction,
                "shortcut": shortcut, "primary_shortcut": shortcut, "shortcuts": [shortcut],
                "bound": True, "category": "组合键", "reason_zh": "当前编辑器中的实际键位",
            })
    return records


def _key_label(key):
    return engine.KEY_NAMES.get(
        key, key.replace("_", " ").title() if len(key) > 2 and not key.startswith("F") else key)


def _event_suffix(binding):
    event = binding.get("event_value", "PRESS")
    mouse = binding.get("map_type") == "MOUSE"
    suffix = {
        "DOUBLE_CLICK": "双击" if mouse else "连按两次", "CLICK_DRAG": "拖动",
        "RELEASE": "松开", "CLICK": "单击" if mouse else "点按", "ANY": "任意事件",
    }.get(event, "按下" if mouse and event == "PRESS" else "")
    if event == "CLICK_DRAG":
        direction = {
            "NORTH": "向上", "NORTH_EAST": "向右上", "EAST": "向右", "SOUTH_EAST": "向右下",
            "SOUTH": "向下", "SOUTH_WEST": "向左下", "WEST": "向左", "NORTH_WEST": "向左上",
        }.get(binding.get("event_direction"))
        if direction:
            suffix = direction + "拖动"
    return " · " + suffix if suffix else ""


def filter_bindings(bindings, held_modifiers, held_keys=(), allow_empty=False):
    """Complete a held modifier/chord prefix without consuming any input.

    Holding Ctrl matches Ctrl+S and Ctrl+Shift+S. Holding Ctrl+Shift narrows the
    list to bindings that permit both keys. Additional required modifiers are
    kept in ``suffix_shortcut``; already held required keys are removed.
    Ordinary held keys are only prefixes for Blender's explicit key_modifier
    bindings (such as D+mouse), never fake sequential G-then-X completions.
    """
    held = {str(mod).lower() for mod in held_modifiers}
    held = held.intersection(MODIFIERS)
    keys = {str(key).upper() for key in held_keys}.difference(MODIFIER_EVENTS)
    if not held and not keys and not allow_empty:
        return []
    rows = []
    for binding in bindings:
        if not binding.get("bound", True):
            continue
        required = set(binding.get("required_modifiers", ()))
        optional = set(binding.get("optional_modifiers", ()))
        unconstrained = bool(binding.get("any_modifiers", False))
        if not unconstrained and not held.issubset(required | optional):
            continue
        key_modifier = binding.get("key_modifier", "NONE")
        if keys and (key_modifier == "NONE" or keys != {key_modifier}):
            continue
        key = binding.get("key", "NONE")
        if (key in MODIFIER_EVENTS and not binding.get("modal")) or key == "NONE":
            continue
        remaining = [mod for mod in MODIFIERS if mod in required and mod not in held]
        parts = [MODIFIER_LABELS[mod] for mod in remaining]
        if key_modifier != "NONE" and key_modifier not in keys:
            parts.append(_key_label(key_modifier))
        parts.append(_key_label(key))
        row = dict(binding)
        row["suffix_shortcut"] = " + ".join(parts) + _event_suffix(binding)
        row["remaining_modifiers"] = remaining
        row["held_modifiers"] = [mod for mod in MODIFIERS if mod in held]
        row["held_keys"] = sorted(keys)
        row["prefix_shortcut"] = " + ".join(
            [MODIFIER_LABELS[mod] for mod in MODIFIERS if mod in held] + [_key_label(key) for key in sorted(keys)])
        row["completion_key"] = key
        rows.append(row)
    rows.sort(key=lambda row: (
        not bool(set(row.get("required_modifiers", ())) & held) and not bool(keys),
        row.get("map_type") == "MOUSE",
        COMPLETION_KEY_PRIORITY.get(row["completion_key"], 99),
        len(row["remaining_modifiers"]),
        row.get("key_modifier", "NONE") != "NONE" and row.get("key_modifier") not in keys,
        row.get("keymap_priority", 99),
        row["suffix_shortcut"], row.get("label_zh", ""), row.get("id", ""),
    ))
    return rows


def _canonical_key(key):
    """Collapse modifier sides for a help keycap; retain raw keys on bindings."""
    mod = MODIFIER_EVENT_NAMES.get(key)
    return mod.upper() if mod else key


def _next_key_label(key):
    mod = key.lower()
    return MODIFIER_LABELS[mod] if mod in MODIFIER_LABELS else _key_label(key)


def _binding_identity(binding):
    identity = binding.get("id") or binding.get("action")
    if identity:
        return str(identity)
    return json.dumps(binding, ensure_ascii=False, sort_keys=True)


ORDINARY_SECTIONS = {
    "branch": ("组合", 0), "file": ("文件", 10), "edit": ("编辑", 20),
    "context": ("工具 / 对象", 30), "view": ("视图", 40),
    "animation": ("动画", 50), "other": ("其他", 60),
}
MODAL_SECTIONS = {
    "modal_axes": ("轴向", 0), "modal_transform": ("变换", 10),
    "modal_helpers": ("辅助", 20), "modal_finish": ("确认 / 取消", 30),
    "modal_other": ("其他", 40), "modal_navigation": ("导航", 50),
}


def _ordinary_section(binding):
    operator = _python_operator_name(binding.get("operator", ""))
    namespace, _, name = operator.partition(".")
    if (namespace in {"file", "import_scene", "export_scene", "import_mesh", "export_mesh"} or
            operator in {"wm.save_mainfile", "wm.save_as_mainfile", "wm.open_mainfile", "wm.read_homefile",
                         "wm.read_factory_settings", "wm.recover_last_session", "wm.recover_auto_save",
                         "wm.link", "wm.append", "wm.quit_blender", "wm.save_homefile", "wm.save_userpref"}):
        return "file"
    if (namespace == "ed" or name.startswith("select") or name in {"copy", "paste", "cut", "undo", "redo",
                                     "copy_buffer", "paste_buffer", "copybuffer", "pastebuffer"}):
        return "edit"
    if (namespace in {"view3d", "view2d"} and name.startswith("view_")) or operator in {
        "view3d.localview", "view3d.toggle_xray", "view3d.toggle_shading",
        "view3d.toggle_rotate", "view3d.rotate", "view3d.move", "view3d.zoom", "view3d.dolly",
        "view3d.view_persportho", "image.view_all", "image.view_selected", "node.view_all", "node.view_selected",
    }:
        return "view"
    if namespace in {"anim", "action", "graph", "nla"} or operator in {
        "screen.animation_play", "screen.frame_jump", "screen.keyframe_jump", "screen.animation_cancel",
    }:
        return "animation"
    if namespace in {"object", "mesh", "curve", "curves", "pose", "armature", "sculpt", "paint",
                     "grease_pencil", "transform", "uv", "node", "sequencer", "image"}:
        return "context"
    if operator == "wm.tool_set_by_id" or "Tool:" in binding.get("keymap", ""):
        return "context"
    if operator in {"wm.call_menu", "wm.call_menu_pie", "wm.call_panel"}:
        menu_name = binding.get("properties", {}).get("name", "")
        if any(word in menu_name.lower() for word in ("view", "shading")) and not any(
                word in menu_name.lower() for word in ("object", "mesh", "add")):
            return "view"
        return "context"
    if operator.startswith("wm.context_"):
        path = binding.get("properties", {}).get("data_path", "")
        return "view" if path.startswith("space_data.") else "context"
    return "other"


def _python_operator_name(identifier):
    if "_OT_" in identifier:
        namespace, name = identifier.split("_OT_", 1)
        return namespace.lower() + "." + name
    return identifier


def _modal_section(row, terminals):
    values = {binding.get("propvalue") for binding in terminals}
    if values and values.issubset({"AXIS_X", "AXIS_Y", "AXIS_Z", "PLANE_X", "PLANE_Y", "PLANE_Z"}):
        return "modal_axes"
    if values and values.issubset({"TRANSLATE", "ROTATE", "RESIZE", "TRACKBALL", "VERT_EDGE_SLIDE", "ROTATE_NORMALS"}):
        return "modal_transform"
    if values and values.issubset({"CONFIRM", "CANCEL"}):
        return "modal_finish"
    if values and values.issubset({"PASSTHROUGH_NAVIGATE"}):
        return "modal_navigation"
    if row["branch"] or row["key"].lower() in MODIFIERS or values & {
        "PRECISION", "SNAP_INV_ON", "SNAP_INV_OFF", "SNAP_ON", "SNAP_OFF", "SNAP_TOGGLE",
    }:
        return "modal_helpers"
    return "modal_other"


def _semantic_signature(binding):
    """Conservative equality for a shared explanation, independent of keycap."""
    props = dict(binding.get("properties") or {})
    operator = _python_operator_name(binding.get("operator", ""))
    modal = bool(binding.get("modal"))
    propvalue = binding.get("propvalue", "") if modal else ""
    family = ""
    if operator == "object.subdivision_set" and not props.get("relative", False):
        level = props.get("level")
        numeric_keys = {name: number for number, name in enumerate(("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE"))}
        numeric_keys.update({"NUMPAD_" + str(n): n for n in range(6)})
        if (isinstance(level, int) and not isinstance(level, bool) and
                level in range(6) and numeric_keys.get(binding.get("key")) == level):
            props.pop("level")
            family = "subdivision_levels"
    # Confirmation/cancellation are native modal values with an identical
    # effect across keyboard/mouse aliases. Other maps retain event details.
    finish = modal and propvalue in {"CONFIRM", "CANCEL"}
    activation = [] if finish else [binding.get("map_type", "KEYBOARD"), binding.get("event_value", "PRESS"),
                                    binding.get("event_direction", "ANY"), binding.get("key_modifier", "NONE"),
                                    binding.get("required_modifiers", []), binding.get("optional_modifiers", []),
                                    bool(binding.get("any_modifiers", False))]
    return [operator, props, modal, propvalue, family, activation], family


def _presentation(row, terminals, is_modal):
    if is_modal:
        section = _modal_section(row, terminals)
        label, order = MODAL_SECTIONS[section]
    elif row["branch"]:
        section = "branch"
        label, order = ORDINARY_SECTIONS[section]
    else:
        section = min((_ordinary_section(binding) for binding in terminals),
                      key=lambda name: ORDINARY_SECTIONS[name][1], default="other")
        label, order = ORDINARY_SECTIONS[section]
    metadata = {"section": section, "section_label": label, "section_order": order,
                "merge_key": "", "merge_label_zh": row["label_zh"], "merge_label_en": row["label_en"]}
    if row["branch"] or not terminals or any(not binding.get("operator") for binding in terminals):
        return metadata
    signatures = [_semantic_signature(binding) for binding in terminals]
    serialized = {json.dumps(signature, sort_keys=True, ensure_ascii=False)
                  for signature, _family in signatures}
    if len(serialized) == 1:
        signature = next(iter(serialized))
        metadata["merge_key"] = "effect_" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]
        if all(family == "subdivision_levels" for _signature, family in signatures):
            metadata["merge_label_zh"] = "细分等级"
            metadata["merge_label_en"] = "Subdivision Level"
    return metadata


def _presentation_sort(row):
    section, key = row["section"], row["key"]
    if section == "modal_axes":
        # Custom keymaps still follow the semantic axis, not their remapped key.
        rank = row.get("_semantic_rank", 99)
    elif section == "modal_transform":
        rank = {"S": 0, "R": 1, "G": 2}.get(key, 99)
    elif section == "modal_helpers":
        rank = {"SHIFT": 0, "CTRL": 1, "ALT": 2, "OSKEY": 3, "HYPER": 4}.get(key, 99)
    elif section == "modal_finish":
        rank = row.get("_semantic_rank", 99)
    elif section == "branch":
        rank = MODIFIERS.index(key.lower()) if key.lower() in MODIFIERS else 99
    else:
        rank = COMPLETION_KEY_PRIORITY.get(key, 99)
    return (row["section_order"], rank, row["merge_key"], row["map_type"] == "MOUSE", row["shortcut"])


def next_steps(bindings, held_modifiers, held_keys=(), allow_empty=False):
    """Group only the keys that can be pressed next for the held prefix.

    An incomplete Ctrl+Shift+S binding contributes the Shift branch while only
    Ctrl is held; S becomes a terminal candidate only once Shift is held too.
    Multiple missing modifiers are independent possible next branches, rather
    than an invented mandatory ordering. Full bindings remain attached as IDs.
    """
    matched = filter_bindings(bindings, held_modifiers, held_keys, allow_empty=allow_empty)
    held = {str(mod).lower() for mod in held_modifiers}.intersection(MODIFIERS)
    keys = {str(key).upper() for key in held_keys}.difference(MODIFIER_EVENTS)
    groups = {}
    for binding in matched:
        missing = [mod.upper() for mod in MODIFIERS
                   if mod in binding.get("required_modifiers", ()) and mod not in held]
        key_modifier = binding.get("key_modifier", "NONE")
        if key_modifier != "NONE" and key_modifier not in keys:
            missing.append(_canonical_key(key_modifier))
        branch = bool(missing)
        next_keys = missing or [_canonical_key(binding["key"])]
        for key in dict.fromkeys(next_keys):
            # A modifier branch and that modifier's native modal action share
            # one next key; retain both meanings if both apply.
            group = groups.setdefault(key, {"key": key, "branches": {}, "terminals": {}})
            bucket = group["branches" if branch else "terminals"]
            bucket.setdefault(_binding_identity(binding), binding)

    rows = []
    is_modal = any(binding.get("modal") for binding in matched)
    for key, group in groups.items():
        branches, terminals = group["branches"], group["terminals"]
        branch = bool(branches)
        unique = dict(terminals)
        unique.update(branches)
        labels_zh = list(dict.fromkeys(str(b.get("label_zh") or b.get("label_en") or "")
                                     for b in terminals.values()))
        labels_en = list(dict.fromkeys(str(b.get("label_en") or b.get("label_zh") or "")
                                     for b in terminals.values()))
        zh = " / ".join(label for label in labels_zh if label)
        en = " / ".join(label for label in labels_en if label)
        if branches:
            zh = (zh + " / " if zh else "") + "更多组合 ({})".format(len(branches))
            en = (en + " / " if en else "") + "More combinations ({})".format(len(branches))
        shortcut = _next_key_label(key)
        # Group identity depends only on the canonical next key. Prefix/state
        # changes should update this row, not invent a new action identity.
        identifier = "step_" + key.lower()
        row = {
            "id": identifier, "action": identifier, "next_key": key, "key": key,
            "shortcut": shortcut, "primary_shortcut": shortcut, "shortcuts": [shortcut],
            "suffix_shortcut": shortcut, "label_zh": zh, "label_en": en,
            "branch": branch, "branch_count": len(branches), "bound": True,
            "bindings": list(unique), "terminal_count": len(terminals),
            "map_type": "MOUSE" if all(b.get("map_type") == "MOUSE" for b in unique.values()) else "KEYBOARD",
            "held_modifiers": [mod for mod in MODIFIERS if mod in held], "held_keys": sorted(keys),
        }
        terminal_bindings = list(terminals.values())
        row.update(_presentation(row, terminal_bindings, is_modal))
        values = {binding.get("propvalue") for binding in terminal_bindings}
        axis_ranks = {"AXIS_X": 0, "PLANE_X": 0, "AXIS_Y": 1, "PLANE_Y": 1, "AXIS_Z": 2, "PLANE_Z": 2}
        row["_semantic_rank"] = min((axis_ranks[v] for v in values if v in axis_ranks), default=(
            0 if "CONFIRM" in values else 1 if "CANCEL" in values else 99))
        rows.append(row)
    rows.sort(key=_presentation_sort)
    for row in rows:
        row.pop("_semantic_rank", None)
    return rows
