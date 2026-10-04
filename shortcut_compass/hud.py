"""Blender-native, always-visible shortcut strip drawn inside edit regions.

The draw callbacks consume an already-built snapshot. They never poll operators,
traverse scene data, or change Blender's active area. Next steps wrap into
additional rows. Only the drag grip and collapse chevron capture mouse events;
while a native modal operation is active, every event passes through.
"""
import math
import re

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader


SPACE_TYPES = (
    "SpaceView3D", "SpaceImageEditor", "SpaceNodeEditor", "SpaceGraphEditor",
    "SpaceDopeSheetEditor", "SpaceNLA", "SpaceSequenceEditor",
)
MOUSE_KEYS = {"鼠标左键": "LEFT", "鼠标中键": "MIDDLE", "鼠标右键": "RIGHT"}


def _pref(preferences, name, fallback):
    return getattr(preferences, name, fallback) if preferences else fallback


def _inside(rect, x, y):
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _horizontal_insets(context):
    """Keep the strip clear of visible tool regions overlapping WINDOW.

    Blender can either overlay TOOLS/UI or place WINDOW beside them. Region
    coordinates let us reserve only the part actually covering WINDOW, so the
    non-overlap preference never reserves the toolbar width twice.
    """
    window_region, area = context.region, context.area
    space = (getattr(context, "space_data", None) or
             getattr(getattr(area, "spaces", None), "active", None))
    left, right = 0.0, 0.0
    window_left = window_region.x
    window_right = window_left + window_region.width
    window_bottom = window_region.y
    window_top = window_bottom + window_region.height
    for region in getattr(area, "regions", ()):
        kind = getattr(region, "type", "")
        if kind not in {"TOOLS", "UI"} or region.width <= 1 or region.height <= 1:
            continue
        visible_property = "show_region_toolbar" if kind == "TOOLS" else "show_region_ui"
        if space is not None and not getattr(space, visible_property, True):
            continue
        if region.y + region.height <= window_bottom or region.y >= window_top:
            continue
        region_right = region.x + region.width
        if kind == "TOOLS" and region.x <= window_left < region_right:
            left = max(left, min(window_region.width, region_right - window_left))
        elif kind == "UI" and region.x < window_right <= region_right:
            right = max(right, min(window_region.width, window_right - region.x))
    return left, right


def _rounded_points(x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    points = []
    for cx, cy, first in (
        (x + width - radius, y + radius, -90),
        (x + width - radius, y + height - radius, 0),
        (x + radius, y + height - radius, 90),
        (x + radius, y + radius, 180),
    ):
        for step in range(5):
            angle = math.radians(first + step * 22.5)
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return points


class NativeHUD:
    """Manage removable draw handlers and small, explicit mouse targets.

    ``snapshot_for_area(area.as_pointer())`` returns a JSON-safe snapshot only
    for the tracked area, or None. ``get_preferences()`` returns addon prefs.
    ``handle_event`` should be called with the hovered area and WINDOW region in
    context (using temp_override if needed). It returns True only for grip or
    chevron, leaving every ordinary shortcut untouched. There is no pagination.
    """

    def __init__(self, snapshot_for_area, get_preferences):
        self.snapshot_for_area = snapshot_for_area
        self.get_preferences = get_preferences
        self.enabled = False
        self.error = ""
        self.layouts = {}
        self._handlers = []
        self._shader = None
        self._collapsed = set()
        self._drag = None
        self._click_capture = None
        self._font_id = 0
        self._font_size = None
        self._width_cache = {}
        self._measure_size = None

    def start(self):
        if self.enabled:
            return
        self.error = ""
        self.enabled = True
        for name in SPACE_TYPES:
            space = getattr(bpy.types, name, None)
            if space is None:
                continue
            try:
                handle = space.draw_handler_add(self._draw, (), "WINDOW", "POST_PIXEL")
                self._handlers.append((space, handle))
            except (RuntimeError, ValueError, TypeError) as exc:
                self.error = "%s: %s" % (name, exc)
        if not self._handlers:
            self.enabled = False

    def stop(self):
        self.enabled = False
        self._drag = None
        self._click_capture = None
        for space, handle in self._handlers:
            try:
                space.draw_handler_remove(handle, "WINDOW")
            except (RuntimeError, ValueError, ReferenceError):
                pass
        self._handlers.clear()
        self.layouts.clear()
        self._collapsed.clear()
        self._shader = None
        self._width_cache.clear()
        self._measure_size = None

    def _measure(self, text):
        value = self._width_cache.get(text)
        if value is None:
            value = blf.dimensions(self._font_id, text)[0]
            if len(self._width_cache) > 512:
                self._width_cache.clear()
            self._width_cache[text] = value
        return value

    def _set_measure_font(self, size):
        if size != self._measure_size:
            self._measure_size = size
            self._width_cache.clear()
        blf.size(self._font_id, size)

    def _fill(self, x, y, width, height, color, radius=0):
        if width <= 0 or height <= 0:
            return
        points = (_rounded_points(x, y, width, height, radius) if radius else
                  [(x, y), (x + width, y), (x + width, y + height), (x, y + height)])
        indices = [(0, index, index + 1) for index in range(1, len(points) - 1)]
        batch = batch_for_shader(self._shader, "TRIS", {"pos": points}, indices=indices)
        self._shader.bind()
        self._shader.uniform_float("color", color)
        batch.draw(self._shader)

    def _text(self, x, y, text, color):
        blf.position(self._font_id, x, y, 0)
        blf.color(self._font_id, *color)
        blf.draw(self._font_id, text)

    def _theme(self, preferences):
        opacity = min(1.0, max(0.2, float(_pref(preferences, "hud_opacity", 0.82))))
        background = (0.075, 0.075, 0.075)
        foreground = (0.78, 0.78, 0.78)
        accent = (0.32, 0.50, 0.70)
        try:
            interface = bpy.context.preferences.themes[0].user_interface
            tooltip = interface.wcol_tooltip
            background = tuple(tooltip.inner[:3])
            foreground = tuple(tooltip.text[:3])
            selected = tuple(interface.wcol_tool.inner_sel[:3])
            if max(selected) - min(selected) >= 0.05:
                accent = selected
        except (AttributeError, IndexError, TypeError):
            pass
        # Use Blender's theme, with quiet text and a slightly stronger key face.
        text = tuple(component * 0.85 for component in foreground) + (1.0,)
        muted = tuple(component * 0.60 for component in foreground) + (1.0,)
        key_text = tuple(foreground) + (1.0,)
        key = tuple(min(1.0, component + 0.065) for component in background) + (min(1.0, opacity + 0.10),)
        border = tuple(min(1.0, component + 0.11) for component in background) + (opacity,)
        branch_key = tuple(base * 0.87 + color * 0.13 for base, color in zip(background, accent)) + (opacity,)
        branch_border = tuple(color * 0.72 + text * 0.28 for color, text in zip(accent, foreground)) + (1.0,)
        prefix_key = tuple(base * 0.72 + color * 0.28 for base, color in zip(background, accent)) + (opacity,)
        prefix_border = tuple(color * 0.88 + text * 0.12 for color, text in zip(accent, foreground)) + (1.0,)
        return dict(background=tuple(background) + (opacity,), text=text, muted=muted,
                    key=key, key_text=key_text, border=border, branch_key=branch_key,
                    branch_border=branch_border, prefix_key=prefix_key, prefix_border=prefix_border)

    def _tokens(self, shortcut):
        # The engine supplies a single verified binding. Never split slashes:
        # Num / and Win / Cmd are legitimate parts of a binding, not separators.
        shortcut = shortcut.replace(" · 按下", "").replace(" · 单击", "")
        return [token for token in re.split(r"( \+ | → | · )", shortcut) if token]

    def _item(self, recommendation, scale):
        # Prefix filtering chooses a specific actual binding, so showing the
        # shortest alternate binding here could contradict the held keys.
        members = recommendation.get("members") or [recommendation]
        tokens = []
        for index, member in enumerate(members):
            shortcut = str(member.get("suffix_shortcut") or member.get("primary_shortcut") or
                           member.get("shortcut", ""))
            if index:
                # This is an explicit alias separator. Slashes *inside* a
                # key's text, such as Num / or Win / Cmd, remain one keycap.
                tokens.append((" / ", True))
            tokens.extend((text, text.strip() in {"+", "→", "·"})
                          for text in self._tokens(shortcut))
        widths = [(19 * scale if text.strip() in MOUSE_KEYS else
                   self._measure(text.strip()) + (5 if separator else 10) * scale)
                  for text, separator in tokens]
        label = str(recommendation.get("label_zh") or recommendation.get("label_en") or "")
        branch = bool(recommendation.get("branch"))
        branch_arrow_width = 13 * scale if branch else 0.0
        section_width = 10 * scale if recommendation.get("section_before") else 0.0
        width = sum(widths) + 7 * scale + self._measure(label) + 17 * scale + branch_arrow_width + section_width
        return dict(tokens=tokens, widths=widths, label=label, width=width,
                    label_width=self._measure(label), key_scale=1.0,
                    label_lines=[label] if label else [], stacked=False,
                    height=30 * scale, branch=branch, branch_accent=branch,
                    branch_arrow_width=branch_arrow_width, section_width=section_width,
                    section=recommendation.get("section", ""),
                    section_label=recommendation.get("section_label", ""),
                    section_before=bool(recommendation.get("section_before")),
                    merge_key=recommendation.get("merge_key", ""),
                    group_id=recommendation.get("group_id"), members=members,
                    action=members[0].get("action"),
                    shortcut=members[0].get("shortcut", ""),
                    suffix_shortcut=members[0].get("suffix_shortcut") or members[0].get("shortcut", ""))

    def _group_candidates(self, candidates):
        """Share labels only for aliases explicitly verified by the engine."""
        groups, shared = [], {}
        for row in candidates:
            merge_key = row.get("merge_key")
            key = (row.get("section", ""), merge_key) if merge_key and not row.get("branch") else None
            if key is not None and key in shared:
                shared[key]["members"].append(row)
                continue
            group = dict(row, members=[row], group_id=len(groups))
            if key is not None:
                group["label_zh"] = row.get("merge_label_zh") or row.get("label_zh", "")
                group["label_en"] = row.get("merge_label_en") or row.get("label_en", "")
                shared[key] = group
            groups.append(group)
        return groups

    def _group_chunks(self, group, available, scale):
        """Split wide alias groups without hiding keys or shrinking all caps."""
        chunks, current = [], []
        for member in group["members"]:
            trial = dict(group, members=current + [member], section_before=False)
            if current and self._item(trial, scale)["width"] > available:
                chunks.append(dict(group, members=current))
                current = []
            current.append(member)
        if current:
            chunks.append(dict(group, members=current))
        return chunks

    def _wrap_text(self, text, available):
        """Wrap complete labels, including Chinese text, without ellipses."""
        lines, current = [], ""
        for character in text:
            if current and self._measure(current + character) > available:
                lines.append(current)
                current = ""
            current += character
        if current:
            lines.append(current)
        return lines

    def _fit_item(self, item, available, scale, size):
        """Wrap a long operation label, preserving both its text and the key."""
        result = dict(item)
        section_width = item["section_width"]
        available = max(1.0, available - section_width)
        keys_width = sum(item["widths"]) + item["branch_arrow_width"]
        line_height = max(size * 1.35, 15 * scale)
        label_room = available - keys_width - 24 * scale
        if item["label"] and label_room < min(48 * scale, self._measure(item["label"])):
            result["stacked"] = True
            label_room = max(1.0, available - 12 * scale)
        else:
            label_room = max(1.0, label_room)
        result["label_lines"] = self._wrap_text(item["label"], label_room)
        result["label_width"] = max((self._measure(line) for line in result["label_lines"]), default=0.0)
        if keys_width + 8 * scale > available:
            result["key_scale"] = available / max(1.0, keys_width + 8 * scale)
            result["widths"] = [width * result["key_scale"] for width in item["widths"]]
            result["branch_arrow_width"] *= result["key_scale"]
        if result["stacked"]:
            result["height"] = 30 * scale + len(result["label_lines"]) * line_height + 4 * scale
        else:
            result["height"] = max(30 * scale, len(result["label_lines"]) * line_height + 10 * scale)
        result["width"] = section_width + min(available, max(sum(result["widths"]) + result["branch_arrow_width"] + 8 * scale,
                                            result["label_width"] + 12 * scale) if result["stacked"] else
                              sum(result["widths"]) + result["branch_arrow_width"] + result["label_width"] + 24 * scale)
        return result

    def _mouse(self, x, y, button, scale, theme):
        """Draw the same simple button-outline language as native status hints."""
        width, height = 13 * scale, 20 * scale
        self._fill(x, y, width, height, theme["text"], 5 * scale)
        self._fill(x + scale, y + scale, width - 2 * scale, height - 2 * scale,
                   theme["key"], 4 * scale)
        self._fill(x + width / 2 - 0.5 * scale, y + height / 2,
                   scale, height / 2 - scale, theme["text"])
        self._fill(x + scale, y + height / 2, width - 2 * scale, scale, theme["text"])
        if button in {"LEFT", "RIGHT"}:
            offset = 2 * scale if button == "LEFT" else width / 2 + scale
            self._fill(x + offset, y + height / 2 + 2 * scale,
                       3.5 * scale, 5.5 * scale, theme["text"], 1.5 * scale)
        else:
            self._fill(x + width / 2 - 1.5 * scale, y + height / 2 + 3 * scale,
                       3 * scale, 5 * scale, theme["key_text"], 1.5 * scale)

    def _pack_rows(self, groups, prefix, content_width, scale, size, common=False):
        """Pack every alias key with a quiet idle title or an active prefix."""
        packed, current, used = [], [], 0.0
        if prefix:
            width = self._measure(prefix) + (16 if common else 34) * scale
            current.append(dict(kind="heading" if common else "prefix", label=prefix,
                                width=min(width, content_width),
                                height=30 * scale, held_accent=not common,
                                text_scale=min(1.0, content_width / max(1.0, width))))
            used = current[0]["width"]
        items = []
        previous_section = None
        for group in groups:
            section = group.get("section", "")
            section_before = previous_section is not None and section != previous_section
            for chunk_index, chunk in enumerate(self._group_chunks(group, content_width, scale)):
                chunk["section_before"] = section_before and chunk_index == 0
                item = self._item(chunk, scale)
                if current and used + item["width"] > content_width:
                    packed.append(current)
                    current, used = [], 0.0
                if item["width"] > content_width:
                    item = self._fit_item(item, content_width, scale, size)
                item["kind"] = "candidate"
                current.append(item)
                items.append(item)
                used += item["width"]
            previous_section = section
        if not groups:
            message = "当前无常用快捷键" if common else "没有可接续的按键"
            message_width = self._measure(message) + 12 * scale
            if current and used + message_width > content_width:
                packed.append(current)
                current, used = [], 0.0
            lines = self._wrap_text(message, max(1.0, content_width - 12 * scale))
            current.append(dict(kind="message", label=message, label_lines=lines,
                                width=min(message_width, content_width),
                                height=max(30 * scale, len(lines) * size * 1.35 + 10 * scale)))
        if current:
            packed.append(current)
        heights = [max(block["height"] for block in row) for row in packed]
        return packed, items, heights

    def _layout(self, context, snapshot, preferences, scale):
        region, area = context.region, context.area
        pointer = area.as_pointer()
        input_state = snapshot.get("input") or {}
        active = bool(input_state.get("active"))
        modal = snapshot.get("modal")
        display_mode = "modal" if modal is not None else "prefix" if active else "common"
        previous = self.layouts.get(pointer)
        if modal is not None or (previous and previous.get("display_mode") == "modal"):
            # A native operation owns confirmation/cancellation. Returning to
            # common shortcuts must not revive a capture from before it began.
            if self._drag and self._drag.get("area") == pointer:
                self._drag = None
            if self._click_capture and self._click_capture.get("area") == pointer:
                self._click_capture = None
        held_prefix = str(input_state.get("prefix_label") or "") if active else ""
        if modal is not None:
            operation = str(modal.get("label_zh") or modal.get("label") or modal.get("operator") or "当前操作")
            prefix = operation + (" · " + held_prefix if held_prefix else "")
        elif active:
            prefix = held_prefix
        else:
            prefix = "常用"
        if display_mode == "common":
            # Common bindings are complete shortcuts. Ignore any suffix field
            # left on a reused presentation row from the prefix tree.
            candidates = [dict(row, suffix_shortcut=row.get("shortcut") or row.get("primary_shortcut"))
                          for row in snapshot.get("common_shortcuts", ())
                          if row.get("bound", True) and (row.get("shortcut") or row.get("primary_shortcut"))]
        else:
            candidates = [row for row in snapshot.get("next_steps", ())
                          if row.get("bound", True) and (row.get("suffix_shortcut") or row.get("shortcut"))]
        groups = self._group_candidates(candidates)
        collapsed = pointer in self._collapsed
        margin = 5 * scale
        inset_left, inset_right = _horizontal_insets(context)
        left_bound = inset_left + margin
        right_bound = region.width - inset_right - margin
        max_width = right_bound - left_bound
        max_height = region.height - margin * 2
        base_size = self._font_size or max(9, min(24, int(_pref(preferences, "font_size", 12)))) * scale
        minimum = min(1.0, max(0.4, 6.0 * scale / base_size))
        factors = list(dict.fromkeys([1.0, 0.9, 0.8, 0.7, 0.6, minimum]))
        factors = [value for value in factors if value >= minimum]
        chosen = None
        for factor in factors:
            draw_scale, size = scale * factor, base_size * factor
            self._set_measure_font(size)
            grip_width, toggle_width = 20 * draw_scale, 22 * draw_scale
            content_width = max_width - grip_width - toggle_width - 3 * draw_scale
            if content_width < max(size, 8 * draw_scale) or max_height < 20 * draw_scale:
                continue
            if collapsed:
                packed, items, heights = [], [], [30 * draw_scale]
            else:
                packed, items, heights = self._pack_rows(groups, prefix, content_width, draw_scale, size,
                                                       common=display_mode == "common")
            height = sum(heights)
            natural_width = max((sum(block["width"] for block in row) for row in packed), default=0.0)
            width = min(max_width, grip_width + toggle_width + 3 * draw_scale + natural_width)
            chosen = (draw_scale, size, grip_width, toggle_width, packed, items, heights, width, height)
            if height <= max_height:
                break
        if chosen is None:
            self.layouts.pop(pointer, None)
            self.error = "编辑区域太小，无法显示快捷键提示；请扩大编辑器"
            return None
        draw_scale, size, grip_width, toggle_width, packed, items, heights, width, required_height = chosen
        overflow = required_height > max_height
        height = min(required_height, max_height)
        diagnostic = ("编辑区域高度不足：全部 %d 项需要 %.0f px，当前只有 %.0f px；请扩大编辑器" %
                      (len(candidates), required_height, max_height)) if overflow else ""
        x = min(max(left_bound, _pref(preferences, "hud_x", 12) * scale), right_bound - width)
        y = min(max(margin, _pref(preferences, "hud_y", 12) * scale), region.height - height - margin)
        # Rows read from top to bottom while the complete strip grows upward
        # from its usual lower-left viewport position.
        consumed = 0.0
        visible_items, visible_rows = [], []
        warning_height = 20 * draw_scale if overflow else 0.0
        for row_index, (row, row_height) in enumerate(zip(packed, heights)):
            row_y = y + height - consumed - row_height
            row_visible = row_y >= y + warning_height - 0.01
            cursor = x + grip_width
            for block in row:
                block.update(x=cursor, y=row_y, row_index=row_index, row_height=row_height,
                             visible=row_visible)
                cursor += block["width"]
                if block["kind"] == "candidate" and row_visible:
                    visible_items.append(block)
            if row_visible:
                visible_rows.append(row)
            consumed += row_height
        toggle_x = x + width - toggle_width
        toggle_y = y + height - min(30 * draw_scale, height)
        layout = dict(
            x=x, y=y, width=width, height=height, scale=draw_scale, ui_scale=scale,
            font_size=size, items=items, rows=packed, visible_rows=visible_rows,
            collapsed=collapsed, active=active, modal=modal, modal_active=modal is not None,
            display_mode=display_mode,
            prefix_label=prefix, held_prefix_label=held_prefix, row_count=len(heights),
            candidate_count=len(candidates),
            visible_count=sum(len(item["members"]) for item in visible_items),
            group_count=len(groups), display_group_count=len(items),
            visible_group_count=len(visible_items),
            displayed_rows=[dict(action=member.get("action"), shortcut=member.get("shortcut", ""),
                                 suffix_shortcut=member.get("suffix_shortcut") or member.get("shortcut", ""),
                                 label_zh=member.get("label_zh", ""), shared_label_zh=item["label"],
                                 branch=bool(member.get("branch")), branch_accent=item["branch_accent"],
                                 section=item["section"], merge_key=item["merge_key"],
                                 group_id=item["group_id"], row_index=item["row_index"],
                                 visible=item["visible"]) for item in items for member in item["members"]],
            overflow=overflow, diagnostic=diagnostic, required_height=required_height,
            inset_left=inset_left, inset_right=inset_right,
            min_x=left_bound, max_x=right_bound - width,
            grip=(x, y, x + grip_width, y + height),
            toggle=(toggle_x, toggle_y, x + width, y + height),
            window=context.window.as_pointer(), region=region.as_pointer(),
            region_x=region.x, region_y=region.y, region_width=region.width,
            region_height=region.height)
        self.layouts[pointer] = layout
        self.error = diagnostic
        return layout

    def _draw(self):
        if not self.enabled:
            return
        context = bpy.context
        if not context.area or not context.region or context.region.type != "WINDOW":
            return
        try:
            snapshot = self.snapshot_for_area(context.area.as_pointer())
            if not snapshot:
                self.layouts.pop(context.area.as_pointer(), None)
                return
            preferences = self.get_preferences()
            scale = max(0.75, min(4.0, context.preferences.system.ui_scale))
            size = max(9, min(24, int(_pref(preferences, "font_size", 12)))) * scale
            if size != self._font_size:
                self._font_size = size
                self._width_cache.clear()
            blf.size(self._font_id, size)
            layout = self._layout(context, snapshot, preferences, scale)
            if layout is None:
                return
            if self._shader is None:
                self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            theme = self._theme(preferences)
            old_blend = gpu.state.blend_get()
            try:
                gpu.state.blend_set("ALPHA")
                self._paint(layout, theme, layout["font_size"])
            finally:
                gpu.state.blend_set(old_blend)
            self.error = layout["diagnostic"]
        except Exception as exc:
            # Retain a readable diagnostic for the settings panel; Blender's
            # frequent draw callbacks must never flood its console with errors.
            self.error = "%s: %s" % (type(exc).__name__, exc)

    def _paint(self, layout, theme, size):
        x, y = layout["x"], layout["y"]
        width, height, scale = layout["width"], layout["height"], layout["scale"]
        self._set_measure_font(size)
        self._fill(x, y, width, height, theme["border"], 5 * scale)
        self._fill(x + scale, y + scale, width - 2 * scale, height - 2 * scale,
                   theme["background"], 4 * scale)
        grip_center = y + height / 2
        for dot_x in (x + 7 * scale, x + 11 * scale):
            for dot_y in (grip_center - 5 * scale, grip_center, grip_center + 5 * scale):
                self._fill(dot_x, dot_y, 1.5 * scale, 1.5 * scale, theme["muted"], 0.75 * scale)
        sample_height = blf.dimensions(self._font_id, "Ag")[1]
        line_height = max(size * 1.35, 15 * scale)
        for row in layout["visible_rows"]:
            for block in row:
                cursor = block["x"]
                band_y = block["y"] + block["row_height"] - 30 * scale
                baseline = band_y + (30 * scale - sample_height) / 2 + 1.5 * scale
                if block["kind"] == "heading":
                    factor = block["text_scale"]
                    blf.size(self._font_id, size * factor)
                    self._text(cursor + 3 * scale, baseline, block["label"], theme["muted"])
                    blf.size(self._font_id, size)
                    continue
                if block["kind"] == "prefix":
                    factor = block["text_scale"]
                    chip_scale = scale * factor
                    chip_width = (self._measure(block["label"]) + 12 * scale) * factor
                    chip_height = 21 * chip_scale
                    blf.size(self._font_id, size * factor)
                    prefix_baseline = band_y + (30 * scale - sample_height * factor) / 2 + 1.5 * chip_scale
                    self._fill(cursor, band_y + (30 * scale - chip_height) / 2,
                               chip_width, chip_height, theme["prefix_border"], 3 * chip_scale)
                    self._fill(cursor + chip_scale, band_y + (30 * scale - chip_height) / 2 + chip_scale,
                               chip_width - 2 * chip_scale, chip_height - 2 * chip_scale,
                               theme["prefix_key"], 2 * chip_scale)
                    self._text(cursor + 6 * chip_scale, prefix_baseline,
                               block["label"], theme["key_text"])
                    self._text(cursor + chip_width + 6 * chip_scale, prefix_baseline,
                               "→", theme["muted"])
                    blf.size(self._font_id, size)
                    continue
                if block["kind"] == "message":
                    for index, line in enumerate(block["label_lines"]):
                        self._text(cursor + 3 * scale, baseline - index * line_height,
                                   line, theme["muted"])
                    continue
                if block["section_before"]:
                    self._fill(cursor + 2 * scale, band_y + 7 * scale,
                               scale, 16 * scale, theme["border"])
                    cursor += block["section_width"]
                key_scale = scale * block["key_scale"]
                key_baseline = band_y + (30 * scale - sample_height * block["key_scale"]) / 2 + 1.5 * key_scale
                blf.size(self._font_id, size * block["key_scale"])
                for (token, separator), token_width in zip(block["tokens"], block["widths"]):
                    token = token.strip()
                    if separator:
                        self._text(cursor + 2.5 * key_scale, key_baseline, token, theme["muted"])
                    elif token in MOUSE_KEYS:
                        self._mouse(cursor + 3 * key_scale,
                                    band_y + (30 * scale - 20 * key_scale) / 2,
                                    MOUSE_KEYS[token], key_scale, theme)
                    else:
                        key_height = 21 * key_scale
                        key_y = band_y + (30 * scale - key_height) / 2
                        if block["branch"]:
                            self._fill(cursor, key_y, token_width - key_scale, key_height,
                                       theme["branch_border"], 3 * key_scale)
                            self._fill(cursor + key_scale, key_y + key_scale,
                                       token_width - 3 * key_scale, key_height - 2 * key_scale,
                                       theme["branch_key"], 2 * key_scale)
                        else:
                            self._fill(cursor, key_y, token_width - key_scale, key_height,
                                       theme["key"], 3 * key_scale)
                        self._text(cursor + 5 * key_scale, key_baseline, token, theme["key_text"])
                    cursor += token_width
                blf.size(self._font_id, size)
                if block["branch"]:
                    self._text(cursor + 2 * key_scale, key_baseline,
                               "›", theme["branch_border"])
                    cursor += block["branch_arrow_width"]
                if block["stacked"]:
                    label_x = block["x"] + block["section_width"] + 5 * scale
                    label_baseline = band_y - line_height + (line_height - sample_height) / 2
                else:
                    label_x, label_baseline = cursor + 7 * scale, baseline
                for index, line in enumerate(block["label_lines"]):
                    self._text(label_x, label_baseline - index * line_height,
                               line, theme["text"])
        if layout["overflow"]:
            message = "空间不足 · %d 项，请扩大编辑器" % layout["candidate_count"]
            room = max(1.0, width - 24 * scale)
            factor = min(1.0, room / max(1.0, self._measure(message)))
            blf.size(self._font_id, size * factor)
            self._text(x + 20 * scale, y + 5 * scale, message, theme["key_text"])
            blf.size(self._font_id, size)
        toggle = layout["toggle"]
        cx, cy = (toggle[0] + toggle[2]) / 2, (toggle[1] + toggle[3]) / 2
        if layout["collapsed"]:
            points = [(cx - 2 * scale, cy - 4 * scale), (cx + 3 * scale, cy),
                      (cx - 2 * scale, cy + 4 * scale)]
        else:
            points = [(cx + 2 * scale, cy - 4 * scale), (cx - 3 * scale, cy),
                      (cx + 2 * scale, cy + 4 * scale)]
        batch = batch_for_shader(self._shader, "TRIS", {"pos": points})
        self._shader.bind()
        self._shader.uniform_float("color", theme["muted"])
        batch.draw(self._shader)

    def handle_event(self, context, event):
        if not self.enabled:
            return False
        window = getattr(context, "window", None)
        if not window:
            return False
        area = getattr(context, "area", None)
        pointers = [area.as_pointer()] if area else []
        for capture in (self._drag, self._click_capture):
            if capture and capture.get("area") not in pointers:
                pointers.append(capture.get("area"))
        snapshots = [self.snapshot_for_area(pointer) for pointer in pointers if pointer is not None]
        snapshots = [snapshot for snapshot in snapshots if snapshot]
        if event.type == "WINDOW_DEACTIVATE":
            self._drag = None
            self._click_capture = None
            return False
        if any(snapshot and snapshot.get("modal") is not None for snapshot in snapshots):
            # Native modal operations own mouse confirmation/cancellation.
            # A HUD drag that preceded the operation must not retain ownership.
            self._drag = None
            self._click_capture = None
            return False
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE"}:
            return False
        if self._drag is not None:
            drag = self._drag
            if window.as_pointer() != drag["window"]:
                return False
            if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
                preferences = self.get_preferences()
                if preferences:
                    scale = drag["scale"]
                    preferences.hud_x = round(max(drag.get("min_x", 5), min(
                        drag["limit_x"], drag["x"] + (event.mouse_x - drag["mouse_x"]) / scale)))
                    preferences.hud_y = round(max(5, min(
                        drag["limit_y"], drag["y"] + (event.mouse_y - drag["mouse_y"]) / scale)))
                return True
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._drag = None
                if self._click_capture:
                    self._click_capture["released"] = True
                return True
            if event.type in {"ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"}:
                self._drag = None
                return event.type != "WINDOW_DEACTIVATE"
            return False
        capture = self._click_capture
        if capture and capture["window"] == window.as_pointer() and event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                # A fresh press starts an unrelated click, even if Blender did
                # not synthesize CLICK after the previous release.
                self._click_capture = None
            elif event.value == "RELEASE" and not capture["released"]:
                capture["released"] = True
                return True
            elif event.value in {"CLICK", "DOUBLE_CLICK"}:
                self._click_capture = None
                return True
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return False
        area, region = getattr(context, "area", None), getattr(context, "region", None)
        if not area or not region or region.type != "WINDOW":
            return False
        pointer = area.as_pointer()
        layout = self.layouts.get(pointer)
        if not layout or layout["window"] != window.as_pointer() or layout["region"] != region.as_pointer():
            return False
        snapshot = self.snapshot_for_area(pointer)
        if not snapshot:
            return False
        local_x, local_y = event.mouse_x - region.x, event.mouse_y - region.y
        if _inside(layout["toggle"], local_x, local_y):
            self._click_capture = dict(window=window.as_pointer(), area=pointer, released=False)
            if pointer in self._collapsed:
                self._collapsed.remove(pointer)
            else:
                self._collapsed.add(pointer)
            area.tag_redraw()
            return True
        if _inside(layout["grip"], local_x, local_y):
            self._click_capture = dict(window=window.as_pointer(), area=pointer, released=False)
            scale = layout["ui_scale"]
            self._drag = dict(window=window.as_pointer(), area=pointer, scale=scale,
                              x=layout["x"] / scale, y=layout["y"] / scale,
                              mouse_x=event.mouse_x, mouse_y=event.mouse_y,
                              min_x=layout["min_x"] / scale,
                              limit_x=layout["max_x"] / scale,
                              limit_y=(region.height - layout["height"]) / scale - 5)
            return True
        return False
