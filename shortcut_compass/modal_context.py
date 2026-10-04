"""Read the running modal operator and its effective user keymap.

Window.modal_operators follows Blender's modal-handler priority order. Native
modal item polls and the active child of a macro are not exposed by Python RNA;
the adapter filters known transform editor/mode exclusions, retains declared
bindings for other maps, and never starts an operator to discover its state.
"""
import hashlib
import json

import bpy

from . import engine, prefix


MODAL_LABELS = {
    "CONFIRM": "确认", "CANCEL": "取消",
    "AXIS_X": "X 轴", "AXIS_Y": "Y 轴", "AXIS_Z": "Z 轴",
    "PLANE_X": "YZ 平面", "PLANE_Y": "XZ 平面", "PLANE_Z": "XY 平面",
    "CONS_OFF": "清除轴限制", "PRECISION": "精细调整（按住）",
    "SNAP_INV_ON": "吸附（按住）", "SNAP_INV_OFF": "吸附（按住）",
    "SNAP_TOGGLE": "切换吸附", "SNAP_ON": "吸附（按住）", "SNAP_OFF": "吸附（按住）",
    "TRANSLATE": "移动", "ROTATE": "旋转", "RESIZE": "缩放", "TRACKBALL": "轨迹球旋转",
    "VERT_EDGE_SLIDE": "顶点 / 边滑移", "ROTATE_NORMALS": "旋转法向",
    "EDIT_SNAP_SOURCE_ON": "设置吸附基点", "EDIT_SNAP_SOURCE_OFF": "设置吸附基点",
    "ADD_SNAP": "添加吸附点", "REMOVE_SNAP": "移除吸附点",
    "PROPORTIONAL_SIZE_UP": "增大衰减范围", "PROPORTIONAL_SIZE_DOWN": "减小衰减范围",
    "PROPORTIONAL_SIZE": "调整衰减范围", "AUTOCONSTRAIN": "自动轴限制",
    "AUTOCONSTRAINPLANE": "自动平面限制", "PASSTHROUGH_NAVIGATE": "视图导航（按住）",
}


def _operator_id(operator):
    identifier = engine._attr(operator, "bl_idname", "")
    if not identifier:
        identifier = engine._attr(engine._attr(engine._attr(operator, "properties"), "bl_rna"), "identifier", "")
    return identifier


def _python_operator(identifier):
    if "_OT_" in identifier:
        namespace, name = identifier.split("_OT_", 1)
        return namespace.lower() + "." + name
    return identifier


def _effective_keymap(keymaps, identifier):
    try:
        linked = keymaps.find_modal(identifier)
        if linked is None:
            return None
        # find_modal returns the operator-type association, independent of the
        # KeyConfig receiver. Resolve its identity in the merged user config.
        return keymaps.find_match(linked) or keymaps.get(linked.name) or linked
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return None


def _operator_labels(operator, identifier):
    label = engine._attr(operator, "name", "") or identifier
    operation = prefix._operator(_python_operator(identifier))
    if operation:
        try:
            rna = operation.get_rna_type()
            label = rna.name
            return prefix._translate(label, engine._attr(rna, "translation_context", "Operator")), label
        except (AttributeError, RuntimeError, TypeError, ReferenceError):
            pass
    return prefix._translate(label), label


def _transform_available(context, operator, identifier, propvalue):
    """Apply only exclusions recoverable from public context/operator fields."""
    editor = engine._attr(engine._attr(context, "area"), "type", "")
    props = engine._attr(operator, "properties")
    if propvalue in {"AXIS_Z", "PLANE_X", "PLANE_Y", "PLANE_Z", "AUTOCONSTRAINPLANE"}:
        if editor in {"IMAGE_EDITOR", "GRAPH_EDITOR", "DOPESHEET_EDITOR", "NLA_EDITOR", "SEQUENCE_EDITOR"}:
            return False
    if propvalue in {"PROPORTIONAL_SIZE", "PROPORTIONAL_SIZE_UP", "PROPORTIONAL_SIZE_DOWN"}:
        if not engine._attr(props, "use_proportional_edit", False):
            return False
    if propvalue.startswith("AUTOIK_CHAIN_LEN_"):
        return False  # Its native T_AUTOIK runtime flag is not exposed.
    if propvalue in {"NODE_ATTACH_ON", "NODE_ATTACH_OFF", "NODE_FRAME", "INSERTOFS_TOGGLE_DIR"}:
        return editor == "NODE_EDITOR"
    if propvalue == "STRIP_CLAMP_TOGGLE":
        return editor == "SEQUENCE_EDITOR"
    if propvalue == "CONS_OFF":
        return any(engine._attr(props, "constraint_axis", (False, False, False)))
    if propvalue == "TRANSLATE" and identifier == "TRANSFORM_OT_translate":
        return False
    if propvalue == "ROTATE" and identifier == "TRANSFORM_OT_rotate":
        return False
    if propvalue == "RESIZE" and identifier == "TRANSFORM_OT_resize":
        return False
    if propvalue == "VERT_EDGE_SLIDE":
        return (identifier == "TRANSFORM_OT_translate" and
                engine._attr(context, "mode", "") == "EDIT_MESH")
    if propvalue == "TRACKBALL":
        return identifier == "TRANSFORM_OT_rotate"
    if propvalue == "ROTATE_NORMALS":
        return identifier == "TRANSFORM_OT_rotate" and engine._attr(context, "mode", "") == "EDIT_MESH"
    if propvalue in {"EDIT_SNAP_SOURCE_ON", "EDIT_SNAP_SOURCE_OFF", "ADD_SNAP", "REMOVE_SNAP"}:
        return False  # Native snap-target/source-edit flags are not exposed.
    return True


def _modal_bindings(context, operator, identifier, keymap):
    names = {item.identifier: item.name for item in engine._items(engine._attr(keymap, "modal_event_values", ()))}
    bindings, seen = [], set()
    transform_map = keymap.name == "Transform Modal Map"
    for item in engine._items(engine._attr(keymap, "keymap_items", ())):
        if not engine._attr(item, "active", False):
            continue
        propvalue = engine._attr(item, "propvalue", "")
        if not propvalue:
            continue
        if transform_map and not _transform_available(context, operator, identifier, propvalue):
            continue
        shortcut = engine._shortcut(item)
        if not shortcut:
            continue
        key = engine._attr(item, "type", "NONE")
        modifiers = tuple(engine._attr(item, mod, 0) for mod in prefix.MODIFIERS)
        any_modifiers = bool(engine._attr(item, "any", False))
        value = engine._attr(item, "value", "PRESS")
        direction = engine._attr(item, "direction", "ANY") if value == "CLICK_DRAG" else "ANY"
        key_modifier = engine._attr(item, "key_modifier", "NONE")
        signature = (propvalue, key, modifiers, any_modifiers, value, direction, key_modifier)
        if signature in seen:
            continue
        seen.add(signature)
        label = names.get(propvalue, propvalue.replace("_", " ").title())
        zh = MODAL_LABELS.get(propvalue, prefix._translate(label))
        # ON/OFF and left/right are one readable held-key operation in the
        # grouped UI, but retain each actual event in the full binding catalog.
        if propvalue in {"SNAP_INV_ON", "SNAP_INV_OFF", "SNAP_ON", "SNAP_OFF"}:
            label = "Snapping (hold)"
        elif propvalue == "PRECISION":
            label = "Precision (hold)"
        uid = "modal_" + hashlib.sha1(json.dumps([identifier, keymap.name, signature],
                                                ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        bindings.append({
            "id": uid, "action": uid, "operator": identifier, "properties": {},
            "label_zh": zh, "label_en": label, "keymap": keymap.name,
            "keymap_priority": 0, "map_type": engine._attr(item, "map_type", "KEYBOARD"),
            "required_modifiers": [mod for mod, val in zip(prefix.MODIFIERS, modifiers) if val is True or val == 1],
            "optional_modifiers": [mod for mod, val in zip(prefix.MODIFIERS, modifiers) if val == -1],
            "any_modifiers": any_modifiers, "key_modifier": key_modifier,
            "key": key, "event_value": value, "event_direction": direction,
            "shortcut": shortcut, "primary_shortcut": shortcut, "shortcuts": [shortcut],
            "bound": True, "modal": True, "propvalue": propvalue,
            "category": "操作中的按键", "reason_zh": "当前模态操作的实际键位",
        })
    return bindings


def active_modal(context):
    """Return the first running native modal map in handler priority order.

    Our passive observer has no semantic role. Other operators without an
    associated modal keymap are skipped, like Blender's native status hints.
    No operator reference is kept after this call: completion/cancel naturally
    removes the result on the next query. Python cannot resolve a native macro's
    private active child, so this function does not guess one from its history.
    """
    window = engine._attr(context, "window")
    configs = engine._attr(engine._attr(context, "window_manager"), "keyconfigs")
    keymaps = engine._attr(engine._attr(configs, "user"), "keymaps")
    if window is None or keymaps is None:
        return None
    for operator in engine._items(engine._attr(window, "modal_operators", ())):
        identifier = _operator_id(operator)
        if not identifier or identifier.upper().startswith("SHORTCUT_COMPASS_OT_"):
            continue
        keymap = _effective_keymap(keymaps, identifier)
        if keymap is None or not engine._attr(keymap, "is_modal", False):
            continue
        label_zh, label_en = _operator_labels(operator, identifier)
        return {
            "operator": identifier, "label_zh": label_zh, "label_en": label_en,
            "keymap": keymap.name, "bindings": _modal_bindings(context, operator, identifier, keymap),
        }
    return None
