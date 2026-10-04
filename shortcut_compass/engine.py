"""Read-only, context-aware shortcut suggestions for Blender.

Call these functions on Blender's main thread, inside the target area's
``context.temp_override``. Shortcut text always comes from the effective user
key configuration. This module never invokes an operator or edits scene data.
"""
from dataclasses import dataclass, field

import bpy
import bmesh


MODE_LABELS = {
    "OBJECT": "物体模式", "EDIT_MESH": "网格编辑", "EDIT_CURVE": "曲线编辑",
    "EDIT_SURFACE": "曲面编辑", "EDIT_ARMATURE": "骨架编辑", "POSE": "姿态模式",
    "SCULPT": "雕刻模式", "PAINT_WEIGHT": "权重绘制", "PAINT_VERTEX": "顶点绘制",
    "PAINT_TEXTURE": "纹理绘制", "EDIT_TEXT": "文本编辑", "EDIT_CURVES": "毛发曲线编辑",
    "SCULPT_CURVES": "毛发曲线雕刻", "EDIT_GREASE_PENCIL": "蜡笔编辑",
    "PAINT_GREASE_PENCIL": "蜡笔绘制", "SCULPT_GREASE_PENCIL": "蜡笔雕刻",
}
EDITOR_LABELS = {
    "VIEW_3D": "3D 视图", "IMAGE_EDITOR": "图像 / UV 编辑器", "NODE_EDITOR": "节点编辑器",
    "GRAPH_EDITOR": "曲线编辑器", "DOPESHEET_EDITOR": "动画 / 时间线", "NLA_EDITOR": "非线性动画",
    "SEQUENCE_EDITOR": "视频序列", "OUTLINER": "大纲视图", "TEXT_EDITOR": "文本编辑器",
    "PROPERTIES": "属性", "FILE_BROWSER": "文件浏览器", "CONSOLE": "控制台",
}


def _attr(value, name, default=None):
    try:
        return getattr(value, name, default)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return default


def _items(value):
    try:
        return list(value) if value is not None else []
    except (TypeError, ReferenceError, RuntimeError):
        return []


def gather_context(context):
    """Return JSON-safe information without synchronizing or altering mesh data."""
    area = _attr(context, "area")
    space = _attr(context, "space_data")
    obj = _attr(context, "active_object")
    mode = _attr(context, "mode", "OBJECT")
    editor = _attr(area, "type", "UNKNOWN")
    selected = _items(_attr(context, "selected_objects", ()))
    settings = _attr(context, "tool_settings")
    mesh_mode = [bool(v) for v in _attr(settings, "mesh_select_mode", (True, False, False))]
    result = {
        "editor": editor, "editor_type": editor, "ui_type": _attr(area, "ui_type", editor),
        "space_mode": _attr(space, "mode", ""),
        "editor_label_zh": EDITOR_LABELS.get(editor, editor),
        "mode": mode, "mode_label_zh": MODE_LABELS.get(mode, mode),
        "active_object": _attr(obj, "name", ""), "object_type": _attr(obj, "type", ""),
        "object_name": _attr(obj, "name", ""),
        "selected_objects": len(selected), "selected_object_count": len(selected),
        "mesh_select_mode": mesh_mode, "selected_vertices": 0, "selected_edges": 0,
        "selected_faces": 0, "selected_bones": 0, "selected_points": 0,
        "selected_nodes": 0, "selected_keyframes": 0, "selected_uvs": 0, "selection_available": False,
        "tool": {"idname": "", "label": ""}, "tool_label": "",
        "frame": _attr(_attr(context, "scene"), "frame_current", 0),
        "keyconfig": "", "keymap_available": False,
    }
    # Multi-object mesh editing: count each unique datablock once, never update it.
    if mode == "EDIT_MESH":
        objects = _items(_attr(context, "objects_in_mode_unique_data", ())) or ([obj] if obj else [])
        for edit_obj in objects:
            if _attr(edit_obj, "type") != "MESH":
                continue
            try:
                totals = [_attr(edit_obj.data, name) for name in ("total_vert_sel", "total_edge_sel", "total_face_sel")]
                bm = None
                if all(isinstance(count, int) for count in totals):
                    for key, count in zip(("selected_vertices", "selected_edges", "selected_faces"), totals):
                        result[key] += count
                else:
                    bm = bmesh.from_edit_mesh(edit_obj.data)
                    result["selected_vertices"] += sum(v.select and not v.hide for v in bm.verts)
                    result["selected_edges"] += sum(e.select and not e.hide for e in bm.edges)
                    result["selected_faces"] += sum(f.select and not f.hide for f in bm.faces)
                if editor == "IMAGE_EDITOR" and result["ui_type"] == "UV":
                    bm = bm or bmesh.from_edit_mesh(edit_obj.data)
                    uv_layer = bm.loops.layers.uv.active
                    if uv_layer:
                        sync = _attr(settings, "use_uv_select_sync", False)
                        sync_valid = _attr(bm, "uv_select_sync_valid", False)
                        result["selected_uvs"] += sum(
                            bool(loop.vert.select if sync and not sync_valid else
                                 _attr(loop, "uv_select_vert", _attr(loop[uv_layer], "select", False)))
                            for face in bm.faces if not face.hide and (sync or face.select)
                            for loop in face.loops)
            except (AttributeError, ReferenceError, RuntimeError, ValueError):
                pass
        result["selection_available"] = result["selected_vertices"] > 0
        if editor == "IMAGE_EDITOR" and result["ui_type"] == "UV":
            result["selection_available"] = result["selected_uvs"] > 0
    elif mode == "EDIT_ARMATURE":
        bones = _items(_attr(context, "selected_editable_bones", ()))
        if not bones:
            bones = [bone for bone in _items(_attr(_attr(obj, "data"), "edit_bones", ()))
                     if not _attr(bone, "hide", False) and
                     (_attr(bone, "select", False) or _attr(bone, "select_head", False) or _attr(bone, "select_tail", False))]
        result["selected_bones"] = len(bones)
        result["selection_available"] = bool(bones)
    elif mode == "POSE":
        bones = _items(_attr(context, "selected_pose_bones", ()))
        if not bones:
            bones = [bone for bone in _items(_attr(_attr(obj, "pose"), "bones", ()))
                     if _attr(_attr(bone, "bone"), "select", False) and
                     not _attr(_attr(bone, "bone"), "hide", False)]
        result["selected_bones"] = len(bones)
        result["selection_available"] = bool(bones)
    elif mode in {"EDIT_CURVE", "EDIT_SURFACE"}:
        for spline in _items(_attr(_attr(obj, "data"), "splines", ())):
            result["selected_points"] += sum(
                bool(_attr(p, "select_control_point", False) or _attr(p, "select_left_handle", False)
                     or _attr(p, "select_right_handle", False))
                for p in _items(_attr(spline, "bezier_points", ())))
            result["selected_points"] += sum(bool(_attr(p, "select", False))
                                               for p in _items(_attr(spline, "points", ())))
        result["selection_available"] = result["selected_points"] > 0
    else:
        result["selection_available"] = bool(selected)
    if editor == "NODE_EDITOR":
        nodes = _items(_attr(context, "selected_nodes", ()))
        result["selected_nodes"] = len(nodes)
        result["selection_available"] = bool(nodes)
    elif editor in {"GRAPH_EDITOR", "DOPESHEET_EDITOR"}:
        # Avoid traversing all curves on every refresh; selected editable curves
        # are exposed by animation editor context, when available.
        curves = _items(_attr(context, "selected_editable_fcurves", ()))
        result["selected_keyframes"] = sum(
            bool(_attr(point, "select_control_point", False))
            for curve in curves for point in _items(_attr(curve, "keyframe_points", ())))
        result["selection_available"] = result["selected_keyframes"] > 0
    workspace = _attr(context, "workspace")
    tools = _attr(workspace, "tools")
    if tools:
        try:
            if editor == "VIEW_3D":
                tool = tools.from_space_view3d_mode(mode, create=False)
            elif editor == "IMAGE_EDITOR":
                tool = tools.from_space_image_mode(_attr(space, "mode", "VIEW"), create=False)
            else:
                tool = None
            if tool:
                tool_id = _attr(tool, "idname", "")
                result["tool"] = {"idname": tool_id, "label": tool_id.split(".")[-1].replace("_", " ")}
                result["tool_label"] = result["tool"]["label"]
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    configs = _attr(_attr(context, "window_manager"), "keyconfigs")
    config = _attr(configs, "user")
    if config:
        result["keyconfig"] = _attr(_attr(configs, "active"), "name", _attr(config, "name", ""))
        result["keymap_available"] = any(bool(_items(_attr(km, "keymap_items", ())))
                                         for km in _items(_attr(config, "keymaps", ())))
    return result


@dataclass(frozen=True)
class Action:
    id: str
    zh: str
    en: str
    operator: str
    category: str = "常用"
    properties: dict = field(default_factory=dict)
    alternatives: tuple = ()
    needs_selection: bool = False


def A(identifier, zh, en, operator, category="常用", properties=None, alternatives=(), selected=False):
    return Action(identifier, zh, en, operator, category, properties or {}, alternatives, selected)


def menu(identifier, zh, en, name, category="常用", selected=False, pie=False):
    return A(identifier, zh, en, "wm.call_menu_pie" if pie else "wm.call_menu", category,
             {"name": name}, selected=selected)


def selection_actions(prefix):
    return [A("select_all", "全选", "Select All", prefix + ".select_all", "选择", {"action": "SELECT"}),
            A("select_none", "取消全选", "Deselect All", prefix + ".select_all", "选择", {"action": "DESELECT"}),
            A("select_invert", "反选", "Invert Selection", prefix + ".select_all", "选择", {"action": "INVERT"})]


TRANSFORMS = [A("move", "移动", "Move", "transform.translate", "变换", {"cursor_transform": False}, selected=True),
              A("rotate", "旋转", "Rotate", "transform.rotate", "变换", selected=True),
              A("scale", "缩放", "Scale", "transform.resize", "变换", selected=True)]

VIEW_ACTIONS = [
    A("frame_selected", "聚焦所选", "Frame Selected", "view3d.view_selected", "视图", selected=True),
    A("frame_all", "显示全部", "Frame All", "view3d.view_all", "视图", {"center": False}),
    A("local_view", "局部视图", "Local View", "view3d.localview", "视图"),
    A("view_front", "前视图", "Front View", "view3d.view_axis", "视图", {"type": "FRONT", "align_active": False}),
    A("view_right", "右视图", "Right View", "view3d.view_axis", "视图", {"type": "RIGHT", "align_active": False}),
    A("view_top", "顶视图", "Top View", "view3d.view_axis", "视图", {"type": "TOP", "align_active": False}),
    A("view_perspective", "透视 / 正交", "Perspective / Orthographic", "view3d.view_persportho", "视图"),
    menu("shading", "视图着色菜单", "Shading Pie", "VIEW3D_MT_shading_pie", "视图", pie=True),
    A("xray", "切换透视选择", "Toggle X-Ray", "view3d.toggle_xray", "视图"),
    A("overlays", "显示 / 隐藏叠加层", "Toggle Overlays", "wm.context_toggle", "视图", {"data_path": "space_data.overlay.show_overlays"}),
]
COMMON = [A("search", "搜索操作", "Operator Search", "wm.search_menu", "通用"),
          A("undo", "撤销", "Undo", "ed.undo", "通用"),
          A("redo", "重做", "Redo", "ed.redo", "通用"),
          A("save", "保存文件", "Save", "wm.save_mainfile", "通用")]


def _candidates(info):
    editor, mode = info["editor"], info["mode"]
    available = info["selection_available"]
    if editor == "VIEW_3D":
        actions = []
        if mode == "OBJECT":
            actions += TRANSFORMS
            actions += [A("duplicate", "复制并移动", "Duplicate Objects", "object.duplicate_move", selected=True),
                        A("duplicate_linked", "关联复制", "Duplicate Linked", "object.duplicate_move_linked", selected=True),
                        menu("apply", "应用变换", "Apply Transforms", "VIEW3D_MT_object_apply", "变换", selected=True)]
            if info["selected_objects"] > 1:
                actions += [A("join", "合并物体", "Join Objects", "object.join", selected=True),
                            A("parent", "设置父级", "Set Parent", "object.parent_set", selected=True)]
            actions += [A("edit_mode", "切换编辑模式", "Toggle Edit Mode", "object.mode_set", "模式",
                          {"mode": "EDIT", "toggle": True}),
                        menu("add", "添加物体", "Add Object", "VIEW3D_MT_add"),
                        A("delete", "删除所选物体", "Delete Objects", "object.delete", properties={"use_global": False}, selected=True),
                        menu("collection", "移动到集合", "Move to Collection", "OBJECT_MT_move_to_collection", selected=True),
                        A("hide", "隐藏所选", "Hide Selected", "object.hide_view_set", "显示", {"unselected": False}, selected=True),
                        A("reveal", "显示隐藏物体", "Reveal Hidden", "object.hide_view_clear", "显示"),
                        A("keyframe_insert", "插入关键帧", "Insert Keyframe", "anim.keyframe_insert", "动画", selected=True)]
            actions += selection_actions("object")
        elif mode == "EDIT_MESH":
            verts, edges, faces = info["selected_vertices"], info["selected_edges"], info["selected_faces"]
            actions += TRANSFORMS
            if available:
                actions += [A("extrude", "挤出并移动", "Extrude Region", "view3d.edit_mesh_extrude_move_normal", "建模",
                              alternatives=(("mesh.extrude_region_move", {}), ("wm.tool_set_by_id", {"name": "builtin.extrude_region"})), selected=True)]
            if faces:
                actions += [A("inset", "内插面", "Inset Faces", "mesh.inset", "建模", alternatives=(("wm.tool_set_by_id", {"name": "builtin.inset_faces"}),), selected=True)]
            if edges and (info["mesh_select_mode"][1] or info["mesh_select_mode"][2]):
                actions += [A("bevel_edges", "边倒角", "Bevel Edges", "mesh.bevel", "建模", {"affect": "EDGES"}, selected=True)]
            elif verts:
                actions += [A("bevel_vertices", "顶点倒角", "Bevel Vertices", "mesh.bevel", "建模", {"affect": "VERTICES"}, selected=True)]
            actions += [A("loopcut", "环切并滑动", "Loop Cut and Slide", "mesh.loopcut_slide", "建模",
                          alternatives=(("wm.tool_set_by_id", {"name": "builtin.loop_cut"}),)),
                        A("knife", "切割工具", "Knife Tool", "mesh.knife_tool", "建模", {"only_selected": False},
                          alternatives=(("wm.tool_set_by_id", {"name": "builtin.knife"}),)),
                        A("duplicate", "复制并移动", "Duplicate Mesh", "mesh.duplicate_move", "建模", selected=True)]
            if verts >= 2:
                actions += [A("fill", "创建边 / 面", "Make Edge / Face", "mesh.edge_face_add", "建模", selected=True),
                            menu("merge", "合并顶点菜单", "Merge Vertices", "VIEW3D_MT_edit_mesh_merge", "建模", selected=True)]
            actions += [A("normals", "重新计算外侧法线", "Recalculate Outside", "mesh.normals_make_consistent", "建模", {"inside": False}, selected=True),
                        A("separate", "分离为新物体", "Separate Mesh", "mesh.separate", "建模", selected=True),
                        A("split", "拆分所选", "Split Selection", "mesh.split", "建模", selected=True),
                        menu("delete", "删除网格菜单", "Delete Mesh", "VIEW3D_MT_edit_mesh_delete", selected=True),
                        A("dissolve", "融并所选", "Dissolve Selection", "mesh.dissolve_mode", "建模", selected=True),
                        A("select_vertex", "顶点选择模式", "Vertex Select", "mesh.select_mode", "选择", {"type": "VERT", "use_extend": False, "use_expand": False}),
                        A("select_edge", "边选择模式", "Edge Select", "mesh.select_mode", "选择", {"type": "EDGE", "use_extend": False, "use_expand": False}),
                        A("select_face", "面选择模式", "Face Select", "mesh.select_mode", "选择", {"type": "FACE", "use_extend": False, "use_expand": False}),
                        A("linked", "选择相连网格", "Select Linked", "mesh.select_linked", "选择", selected=True),
                        A("loop_select", "选择边循环", "Select Edge Loop", "mesh.loop_select", "选择", {"extend": False, "deselect": False, "toggle": False}),
                        A("ring_select", "选择边环", "Select Edge Ring", "mesh.edgering_select", "选择", {"extend": False, "deselect": False, "toggle": False}),
                        menu("uv_unwrap", "UV 展开菜单", "UV Mapping", "VIEW3D_MT_uv_map", "UV", selected=True),
                        A("hide", "隐藏所选网格", "Hide Selected", "mesh.hide", "显示", {"unselected": False}, selected=True),
                        A("reveal", "显示隐藏网格", "Reveal Hidden", "mesh.reveal", "显示"),
                        A("edit_mode", "退出编辑模式", "Toggle Edit Mode", "object.mode_set", "模式", {"mode": "EDIT", "toggle": True}),
                        menu("add", "添加网格", "Add Mesh", "VIEW3D_MT_mesh_add")]
            actions += selection_actions("mesh")
        elif mode in {"EDIT_CURVE", "EDIT_SURFACE"}:
            actions += TRANSFORMS + [
                A("extrude", "挤出曲线", "Extrude Curve", "curve.extrude_move", "建模", selected=True),
                A("duplicate", "复制曲线点", "Duplicate Curve", "curve.duplicate_move", "建模", selected=True),
                A("segment", "连接曲线点", "Make Segment", "curve.make_segment", "建模", selected=True),
                A("handle", "设置控制柄类型", "Set Handle Type", "curve.handle_type_set", "建模", selected=True),
                A("cyclic", "切换闭合曲线", "Toggle Cyclic", "curve.cyclic_toggle", "建模", selected=True),
                A("tilt", "倾斜", "Tilt", "transform.tilt", "变换", selected=True),
                menu("delete", "删除曲线点", "Delete Curve", "VIEW3D_MT_edit_curve_delete", selected=True),
                A("edit_mode", "退出编辑模式", "Toggle Edit Mode", "object.mode_set", "模式", {"mode": "EDIT", "toggle": True})]
            actions += selection_actions("curve")
        elif mode == "EDIT_ARMATURE":
            actions += TRANSFORMS + [
                A("extrude", "挤出骨骼", "Extrude Bones", "armature.extrude_move", selected=True),
                A("duplicate", "复制骨骼", "Duplicate Bones", "armature.duplicate_move", selected=True),
                A("subdivide", "细分骨骼", "Subdivide Bones", "armature.subdivide", selected=True),
                A("parent", "设置骨骼父级", "Parent Bones", "armature.parent_set", selected=True),
                A("roll", "调整骨骼滚转", "Bone Roll", "transform.transform", "变换", {"mode": "BONE_ROLL"}, selected=True),
                A("delete", "删除骨骼", "Delete Bones", "armature.delete", selected=True),
                A("edit_mode", "退出编辑模式", "Toggle Edit Mode", "object.mode_set", "模式", {"mode": "EDIT", "toggle": True})]
            actions += selection_actions("armature")
        elif mode == "POSE":
            actions += TRANSFORMS + [
                A("clear_location", "清除位置", "Clear Location", "pose.loc_clear", "姿态", selected=True),
                A("clear_rotation", "清除旋转", "Clear Rotation", "pose.rot_clear", "姿态", selected=True),
                A("clear_scale", "清除缩放", "Clear Scale", "pose.scale_clear", "姿态", selected=True),
                A("copy_pose", "复制姿态", "Copy Pose", "pose.copy", "姿态", selected=True),
                A("paste_pose", "粘贴姿态", "Paste Pose", "pose.paste", "姿态", {"flipped": False}),
                A("keyframe_insert", "插入关键帧", "Insert Keyframe", "anim.keyframe_insert", "动画", selected=True)]
            actions += selection_actions("pose")
        elif mode in {"SCULPT", "PAINT_TEXTURE", "PAINT_VERTEX", "PAINT_WEIGHT", "SCULPT_CURVES"}:
            brush_kind = {"SCULPT": "SCULPT", "PAINT_TEXTURE": "TEXTURE_PAINT", "PAINT_VERTEX": "VERTEX_PAINT", "PAINT_WEIGHT": "WEIGHT_PAINT", "SCULPT_CURVES": "SCULPT_CURVES"}[mode]
            brush_path = "tool_settings." + {"SCULPT": "sculpt", "PAINT_TEXTURE": "image_paint", "PAINT_VERTEX": "vertex_paint", "PAINT_WEIGHT": "weight_paint", "SCULPT_CURVES": "curves_sculpt"}[mode] + ".brush"
            stroke_op = {"SCULPT": "sculpt.brush_stroke", "PAINT_TEXTURE": "paint.image_paint", "PAINT_VERTEX": "paint.vertex_paint", "PAINT_WEIGHT": "paint.weight_paint", "SCULPT_CURVES": "sculpt_curves.brush_stroke"}[mode]
            actions += [A("brush_size", "调整笔刷大小", "Brush Radius", "wm.radial_control", "笔刷",
                          {"data_path_primary": brush_path + ".size"}),
                        A("brush_strength", "调整笔刷强度", "Brush Strength", "wm.radial_control", "笔刷",
                          {"data_path_primary": brush_path + ".strength"}),
                        A("brush_stroke", "笔刷绘制", "Brush Stroke", stroke_op, "笔刷", {"mode": "NORMAL", "brush_toggle": "None"}),
                        A("brush_invert", "反向笔刷", "Invert Brush", stroke_op, "笔刷", {"mode": "INVERT", "brush_toggle": "None"}),
                        A("brush_smooth", "临时平滑笔刷", "Temporary Smooth Brush", stroke_op, "笔刷", {"brush_toggle": "SMOOTH"}),
                        A("brush_asset", "切换笔刷资源", "Brush Asset Shelf", "wm.call_asset_shelf_popover", "笔刷",
                          {"name": "VIEW3D_AST_brush_" + {"SCULPT": "sculpt", "TEXTURE_PAINT": "texture_paint", "VERTEX_PAINT": "vertex_paint", "WEIGHT_PAINT": "weight_paint", "SCULPT_CURVES": "sculpt_curves"}[brush_kind]}),
                        A("mode_menu", "模式切换菜单", "Mode Pie", "view3d.object_mode_pie_or_toggle", "模式")]
            if mode == "SCULPT":
                actions += [A("mask_clear", "清除遮罩", "Clear Mask", "paint.mask_flood_fill", "雕刻", {"mode": "VALUE", "value": 0.0}),
                            A("mask_invert", "反转遮罩", "Invert Mask", "paint.mask_flood_fill", "雕刻", {"mode": "INVERT"}),
                            A("mask_box", "框选遮罩", "Box Mask", "paint.mask_box_gesture", "雕刻"),
                            A("hide_show", "显示所有雕刻面", "Show All", "paint.hide_show_all", "雕刻", {"action": "SHOW"}),
                            A("dyntopo", "切换动态拓扑", "Toggle Dyntopo", "sculpt.dynamic_topology_toggle", "雕刻")]
            elif mode == "PAINT_WEIGHT":
                actions += [A("sample_weight", "采样权重", "Sample Weight", "paint.weight_sample", "笔刷"),
                            A("sample_group", "采样顶点组", "Sample Vertex Group", "paint.weight_sample_group", "笔刷"),
                            A("weight_gradient", "权重渐变", "Weight Gradient", "paint.weight_gradient", "笔刷")]
        else:
            # Specialized and future modes receive valid view/global actions.
            actions += []
        actions += VIEW_ACTIONS
    elif editor == "IMAGE_EDITOR" and info["ui_type"] == "UV":
        actions = TRANSFORMS + [
            menu("unwrap", "UV 展开菜单", "Unwrap UV", "IMAGE_MT_uvs_unwrap", "UV", selected=True),
            A("pin", "钉住 UV", "Pin UV", "uv.pin", "UV", {"clear": False}, selected=True),
            A("unpin", "取消钉住 UV", "Unpin UV", "uv.pin", "UV", {"clear": True}, selected=True),
            A("pack", "打包 UV 岛", "Pack Islands", "uv.pack_islands", "UV", selected=True),
            A("average_scale", "统一 UV 岛比例", "Average Islands Scale", "uv.average_islands_scale", "UV", selected=True),
            A("linked", "选择相连 UV", "Select Linked UV", "uv.select_linked", "选择"),
            A("stitch", "缝合 UV", "Stitch UV", "uv.stitch", "UV", selected=True),
            A("frame_selected", "聚焦所选 UV", "Frame Selected", "image.view_selected", "视图", selected=True),
            A("frame_all", "显示全部 UV", "Frame All", "image.view_all", "视图")]
        actions += selection_actions("uv")
    elif editor == "NODE_EDITOR":
        actions = [A("node_move", "移动节点", "Move Nodes", "transform.translate", "节点", selected=True),
                   A("duplicate", "复制节点", "Duplicate Nodes", "node.duplicate_move", "节点", selected=True),
                   menu("add", "添加节点", "Add Node", "NODE_MT_add", "节点"),
                   A("delete", "删除节点", "Delete Nodes", "node.delete", "节点", selected=True),
                   A("mute", "禁用 / 启用节点", "Toggle Mute", "node.mute_toggle", "节点", selected=True),
                   A("group", "创建节点组", "Make Node Group", "node.group_make", "节点", selected=True),
                   A("group_edit", "进入 / 退出节点组", "Edit Node Group", "node.group_edit", "节点", {"exit": False}, selected=True),
                   A("ungroup", "解散节点组", "Ungroup Nodes", "node.group_ungroup", "节点", selected=True),
                   A("frame_selected", "聚焦所选节点", "Frame Selected", "node.view_selected", "视图", selected=True),
                   A("frame_all", "显示全部节点", "Frame All", "node.view_all", "视图"),
                   A("link_detach", "断开节点链接", "Detach Links", "node.links_cut", "节点")]
        actions += selection_actions("node")
    elif editor == "IMAGE_EDITOR":
        actions = [A("frame_all", "显示整张图像", "View All", "image.view_all", "视图"),
                   A("zoom_fit", "适应窗口", "Fit Image", "image.view_zoom_ratio", "视图", {"ratio": 1.0})]
        if info["mode"] == "PAINT_TEXTURE" or info.get("space_mode") == "PAINT":
            actions = [A("brush_size", "调整笔刷大小", "Brush Radius", "wm.radial_control", "笔刷",
                         {"data_path_primary": "tool_settings.image_paint.brush.size"}),
                       A("brush_strength", "调整笔刷强度", "Brush Strength", "wm.radial_control", "笔刷",
                         {"data_path_primary": "tool_settings.image_paint.brush.strength"}),
                       A("brush_stroke", "笔刷绘制", "Brush Stroke", "paint.image_paint", "笔刷", {"mode": "NORMAL", "brush_toggle": "None"}),
                       A("brush_smooth", "临时平滑笔刷", "Temporary Smooth Brush", "paint.image_paint", "笔刷", {"brush_toggle": "SMOOTH"}),
                       A("color_flip", "交换笔刷颜色", "Swap Brush Colors", "paint.brush_colors_flip", "笔刷"),
                       A("sample_color", "采样颜色", "Sample Color", "paint.sample_color", "笔刷", {"merged": False})] + actions
    elif editor in {"GRAPH_EDITOR", "DOPESHEET_EDITOR"}:
        prefix = "graph" if editor == "GRAPH_EDITOR" else "action"
        actions = [A("play", "播放 / 暂停", "Play / Pause", "screen.animation_play", "动画", {"reverse": False}),
                   A("next_key", "下一关键帧", "Next Keyframe", "screen.keyframe_jump", "动画", {"next": True}),
                   A("prev_key", "上一关键帧", "Previous Keyframe", "screen.keyframe_jump", "动画", {"next": False})]
        if info["ui_type"] != "TIMELINE":
            actions += TRANSFORMS + [
                A("duplicate", "复制关键帧", "Duplicate Keyframes", prefix + ".duplicate_move", "动画", selected=True),
                A("delete", "删除关键帧", "Delete Keyframes", prefix + ".delete", "动画", selected=True),
                A("interpolation", "设置插值类型", "Set Interpolation", prefix + ".interpolation_type", "动画", selected=True),
                A("handle", "设置控制柄类型", "Set Handle Type", prefix + ".handle_type", "动画", selected=True),
                A("frame_selected", "聚焦所选关键帧", "Frame Selected", prefix + ".view_selected", "视图", selected=True),
                A("frame_all", "显示全部关键帧", "Frame All", prefix + ".view_all", "视图")]
            actions += selection_actions(prefix)
        actions += [A("jump_start", "跳到起始帧", "Jump to First Frame", "screen.frame_jump", "动画", {"end": False}),
                    A("jump_end", "跳到结束帧", "Jump to Last Frame", "screen.frame_jump", "动画", {"end": True})]
    elif editor == "SEQUENCE_EDITOR":
        actions = [A("play", "播放 / 暂停", "Play / Pause", "screen.animation_play", "动画", {"reverse": False}),
                   A("move", "移动片段", "Move Strips", "transform.seq_slide", "序列"),
                   A("duplicate", "复制片段", "Duplicate Strips", "sequencer.duplicate_move", "序列"),
                   A("split", "切分片段", "Split Strips", "sequencer.split", "序列", {"type": "SOFT"}),
                   A("delete", "删除片段", "Delete Strips", "sequencer.delete", "序列"),
                   A("frame_all", "显示全部片段", "Frame All", "sequencer.view_all", "视图")]
        actions += selection_actions("sequencer")
    else:
        actions = []
    # Bindings and polls are the final gate; recommendations aren't predictions
    # of user intent, and never execute their suggested operation.
    return [a for a in actions + COMMON if not a.needs_selection or available]


def _keymap_names(info):
    editor, mode = info["editor"], info["mode"]
    if editor == "VIEW_3D":
        modes = {"OBJECT": "Object Mode", "EDIT_MESH": "Mesh", "EDIT_CURVE": "Curve", "EDIT_SURFACE": "Curve",
                 "EDIT_ARMATURE": "Armature", "POSE": "Pose", "SCULPT": "Sculpt", "PAINT_TEXTURE": "Image Paint",
                 "PAINT_WEIGHT": "Weight Paint", "PAINT_VERTEX": "Vertex Paint", "SCULPT_CURVES": "Sculpt Curves",
                 "EDIT_CURVES": "Curves", "EDIT_TEXT": "Font", "EDIT_GREASE_PENCIL": "Grease Pencil Edit Mode",
                 "PAINT_GREASE_PENCIL": "Grease Pencil Paint Mode"}
        names = [modes.get(mode, ""), "Object Non-modal", "3D View", "3D View Generic"]
    elif editor == "IMAGE_EDITOR":
        names = ["UV Editor", "Image", "Image Generic"] if info["ui_type"] == "UV" else ["Image Paint", "Image", "Image Generic"]
    elif editor == "NODE_EDITOR":
        names = ["Node Editor", "Node Generic"]
    elif editor == "GRAPH_EDITOR":
        names = ["Graph Editor", "Graph Editor Generic", "Animation Channels", "Animation"]
    elif editor == "DOPESHEET_EDITOR":
        names = ["Dopesheet", "Dopesheet Generic", "Animation Channels", "Animation"]
    elif editor == "SEQUENCE_EDITOR":
        names = ["Sequencer", "SequencerCommon", "SequencerPreview"]
    else:
        names = []
    return [n for n in names + ["Frames", "Screen", "Window"] if n]


KEY_NAMES = {"SPACE": "Space", "TAB": "Tab", "RET": "Enter", "NUMPAD_ENTER": "Num Enter", "ESC": "Esc",
             "BACK_SPACE": "Backspace", "DEL": "Delete", "LEFTMOUSE": "鼠标左键", "RIGHTMOUSE": "鼠标右键",
             "MIDDLEMOUSE": "鼠标中键", "WHEELUPMOUSE": "滚轮向上", "WHEELDOWNMOUSE": "滚轮向下",
             "ACCENT_GRAVE": "`", "PERIOD": ".", "COMMA": ",", "MINUS": "-", "EQUAL": "=",
             "LEFT_BRACKET": "[", "RIGHT_BRACKET": "]", "SEMI_COLON": ";", "QUOTE": "'", "SLASH": "/",
             "BACK_SLASH": "\\", "NUMPAD_PLUS": "Num +", "NUMPAD_MINUS": "Num -", "NUMPAD_PERIOD": "Num .",
             "NUMPAD_SLASH": "Num /", "NUMPAD_ASTERIX": "Num *", "HOME": "Home", "END": "End",
             "PAGE_UP": "Page Up", "PAGE_DOWN": "Page Down", "UP_ARROW": "↑", "DOWN_ARROW": "↓",
             "LEFT_ARROW": "←", "RIGHT_ARROW": "→", "LEFT_CTRL": "左 Ctrl", "RIGHT_CTRL": "右 Ctrl",
             "LEFT_SHIFT": "左 Shift", "RIGHT_SHIFT": "右 Shift", "LEFT_ALT": "左 Alt", "RIGHT_ALT": "右 Alt"}
for _word, _digit in zip(("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"), "0123456789"):
    KEY_NAMES[_word] = _digit
    KEY_NAMES["NUMPAD_" + _digit] = "Num " + _digit


def _shortcut(item):
    event_type = _attr(item, "type", "NONE")
    if event_type in {"NONE", "TIMER", "TIMER0", "TIMER1", "TIMER2", "WINDOW_DEACTIVATE", "MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "MOUSESMARTZOOM"}:
        return ""
    if _attr(item, "map_type", "KEYBOARD") not in {"KEYBOARD", "MOUSE"}:
        return ""
    # Blender stores -1 for an unconstrained modifier. Do not mislabel -1 as
    # a required held key simply because bool(-1) is true.
    parts = []
    if _attr(item, "any", False):
        parts.append("任意修饰键")
    else:
        for attr, label in (("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt"), ("oskey", "Win / Cmd"), ("hyper", "Hyper")):
            value = _attr(item, attr, False)
            if value is True or value == 1:
                parts.append(label)
            elif value == -1:
                parts.append(label + "可选")
    modifier = _attr(item, "key_modifier", "NONE")
    if modifier != "NONE":
        parts.append(KEY_NAMES.get(modifier, modifier))
    parts.append(KEY_NAMES.get(event_type, event_type.replace("_", " ").title() if len(event_type) > 2 and not event_type.startswith("F") else event_type))
    result = " + ".join(parts)
    value = _attr(item, "value", "PRESS")
    mouse = _attr(item, "map_type", "KEYBOARD") == "MOUSE"
    suffix = {"DOUBLE_CLICK": "双击" if mouse else "连按两次", "CLICK_DRAG": "拖动", "RELEASE": "松开", "CLICK": "单击" if mouse else "点按", "ANY": "任意事件"}.get(value)
    if value == "CLICK_DRAG":
        direction = _attr(item, "direction", "ANY")
        direction_label = {"NORTH": "向上", "NORTH_EAST": "向右上", "EAST": "向右", "SOUTH_EAST": "向右下",
                           "SOUTH": "向下", "SOUTH_WEST": "向左下", "WEST": "向左", "NORTH_WEST": "向左上"}.get(direction)
        if direction_label:
            suffix = direction_label + "拖动"
    if suffix:
        result += " · " + suffix
    elif mouse and value == "PRESS":
        result += " · 按下"
    return result


def _properties_match(properties, required):
    for name, expected in required.items():
        actual = _attr(properties, name, object())
        if isinstance(expected, dict):
            if not _properties_match(actual, expected):
                return False
        elif actual != expected:
            return False
    return True


def _binding(action, keymaps, names):
    matches = []
    variants = ((action.operator, action.properties),) + action.alternatives
    for rank, name in enumerate(names):
        try:
            keymap = keymaps.get(name)
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if keymap is None or _attr(keymap, "is_modal", False):
            continue
        for item in _items(_attr(keymap, "keymap_items", ())):
            if not _attr(item, "active", False):
                continue
            for operator, properties in variants:
                if _attr(item, "idname") != operator or not _properties_match(_attr(item, "properties"), properties):
                    continue
                shortcut = _shortcut(item)
                if shortcut:
                    mouse = _attr(item, "map_type") == "MOUSE"
                    complicated = sum(_attr(item, k, False) is True or _attr(item, k, False) == 1 for k in ("ctrl", "shift", "alt", "oskey"))
                    matches.append((rank, mouse, complicated, shortcut, name, operator, properties))
                break
    if not matches:
        return None
    matches.sort(key=lambda m: (m[0], m[1], m[2], m[3]))
    best = matches[0]
    # Display a second equivalent binding if useful, without mixing menus,
    # selector actions, or a different operator's properties.
    shortcuts = [best[3]]
    for match in matches[1:]:
        if match[5:] == best[5:] and match[3] not in shortcuts:
            shortcuts.append(match[3])
        if len(shortcuts) == 2:
            break
    return {"shortcut": " / ".join(shortcuts), "shortcuts": shortcuts, "primary_shortcut": best[3],
            "keymap": best[4], "operator": best[5], "properties": best[6]}


def _poll(operator):
    try:
        module, name = operator.split(".", 1)
        op = getattr(getattr(bpy.ops, module), name)
        return bool(op.poll())
    except (AttributeError, ReferenceError, RuntimeError, ValueError, TypeError):
        return False


def _path_exists(context, path):
    try:
        value = context
        for name in path.split("."):
            value = getattr(value, name)
        return value is not None
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _modal_shortcut(keymaps, propvalue):
    try:
        keymap = keymaps.get("Transform Modal Map")
    except (AttributeError, ReferenceError, RuntimeError):
        return None
    if keymap is None or not _attr(keymap, "is_modal", False):
        return None
    matches = []
    for item in _items(_attr(keymap, "keymap_items", ())):
        if _attr(item, "active", False) and _attr(item, "propvalue") == propvalue:
            label = _shortcut(item)
            if label:
                # Unconstrained modifiers mean users need not hold one. This
                # explanation is unnecessary for a sequential held Shift hint.
                label = label.removeprefix("任意修饰键 + ")
                if propvalue == "PRECISION":
                    label = label.replace(" · 任意事件", "") + "（按住）"
                matches.append(label)
    return min(matches, key=lambda s: (len(s), s)) if matches else None


def _sequences(info, results, keymaps, primary_shortcuts):
    if info["editor"] != "VIEW_3D" or not info["selection_available"] or not keymaps:
        return []
    base = {r["action"]: r for r in results if r["bound"]}
    definitions = [("move_x", "沿 X 轴移动", "Move Along X", "move", "AXIS_X"),
                   ("move_y", "沿 Y 轴移动", "Move Along Y", "move", "AXIS_Y"),
                   ("move_z", "沿 Z 轴移动", "Move Along Z", "move", "AXIS_Z"),
                   ("move_xy", "在 XY 平面移动", "Move in XY Plane", "move", "PLANE_Z"),
                   ("rotate_z", "绕 Z 轴旋转", "Rotate Around Z", "rotate", "AXIS_Z")]
    rows = []
    for action_id, zh, en, base_id, propvalue in definitions:
        row = base.get(base_id)
        modal = _modal_shortcut(keymaps, propvalue)
        if not row or not modal:
            continue
        rows.append({"action": action_id, "label_zh": zh, "label_en": en,
                     "operator": row["operator"], "properties": row["properties"],
                     "shortcut": primary_shortcuts[base_id] + " → " + modal,
                     "primary_shortcut": primary_shortcuts[base_id] + " → " + modal,
                     "shortcuts": [primary_shortcuts[base_id] + " → " + modal],
                     "reason_zh": "依次按键：先启动" + ("移动" if base_id == "move" else "旋转") + "，再限制" + ("平面" if propvalue.startswith("PLANE") else "轴向"),
                     "keymap": row["keymap"] + " → Transform Modal Map", "category": "操作组合", "bound": True})
    return rows


def _recommend(context, info, limit=40):
    configs = _attr(_attr(context, "window_manager"), "keyconfigs")
    keymaps = _attr(_attr(configs, "user"), "keymaps")
    names = _keymap_names(info)
    search = _binding(COMMON[0], keymaps, names) if keymaps else None
    results = []
    primary_shortcuts = {}
    for action in _candidates(info):
        if not _poll(action.operator):
            continue
        if action.operator == "wm.radial_control" and not _path_exists(context, action.properties.get("data_path_primary", "")):
            continue
        if action.operator == "wm.context_toggle" and not _path_exists(context, action.properties.get("data_path", "")):
            continue
        binding = _binding(action, keymaps, names) if keymaps else None
        # Alternate bindings that activate tools have their own poll function.
        if binding and not _poll(binding["operator"]):
            binding = None
        if binding:
            primary_shortcuts[action.id] = binding["primary_shortcut"]
        reason = info["mode_label_zh"] if info["editor"] == "VIEW_3D" else info["editor_label_zh"]
        if action.needs_selection:
            if info["mode"] == "EDIT_MESH":
                reason += " · 已选 {} 点 / {} 边 / {} 面".format(info["selected_vertices"], info["selected_edges"], info["selected_faces"])
            elif info["editor"] == "NODE_EDITOR":
                reason += " · 已选 {} 个节点".format(info["selected_nodes"])
            elif info["mode"] in {"POSE", "EDIT_ARMATURE"}:
                reason += " · 已选 {} 根骨骼".format(info["selected_bones"])
            elif info["editor"] in {"GRAPH_EDITOR", "DOPESHEET_EDITOR"}:
                reason += " · 已选 {} 个关键帧".format(info["selected_keyframes"])
            else:
                reason += " · 当前有选择"
        fallback = (search["shortcut"] + " → " if search else "") + "搜索 “" + action.en + "”"
        results.append({
            "action": action.id, "label_zh": action.zh, "label_en": action.en,
            "operator": binding["operator"] if binding else action.operator,
            "properties": binding["properties"] if binding else action.properties,
            "shortcut": binding["shortcut"] if binding else fallback,
            "primary_shortcut": binding["primary_shortcut"] if binding else "",
            "shortcuts": binding["shortcuts"] if binding else [],
            "reason_zh": reason, "keymap": binding["keymap"] if binding else "",
            "category": action.category, "bound": binding is not None,
        })
    # Keep core context actions first, then view/global actions. Unbound entries
    # are still useful but should not crowd real shortcuts out of the display.
    # Place verified sequences directly after the basic transform operations.
    combinations = _sequences(info, results, keymaps, primary_shortcuts)
    insert_at = min(3, len(results))
    results[insert_at:insert_at] = combinations
    results.sort(key=lambda r: not r["bound"])
    return results[:max(0, int(limit))]


def build_snapshot(context):
    info = gather_context(context)
    return {"schema_version": 1, "context": info, "recommendations": _recommend(context, info)}


def recommend(context, limit=40):
    """Suggest valid operations and their current bindings for this context."""
    return _recommend(context, gather_context(context), limit)
