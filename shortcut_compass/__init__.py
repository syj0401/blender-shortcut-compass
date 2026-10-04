"""Always show common shortcuts, then the next keys or active modal controls."""

bl_info = {
    "name": "Shortcut Compass / 快捷键助手",
    "author": "Shortcut Compass contributors",
    "version": (0, 6, 0),
    "blender": (4, 2, 0),
    "location": "Window menu; N sidebar > 快捷键",
    "description": "Keep common shortcuts visible and expand the held-key branches",
    "category": "Interface",
}

import secrets
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from . import engine, prefix, modal_context
from .common_shortcuts import common_shortcuts
from .input_state import HeldInput, sample_platform_modifiers
from .hud import NativeHUD
from .local_service import SnapshotService

SUPPORTED_EDITORS = {
    "VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR", "GRAPH_EDITOR",
    "DOPESHEET_EDITOR", "NLA_EDITOR", "SEQUENCE_EDITOR",
}


def preferences(context=None):
    context = context or bpy.context
    item = context.preferences.addons.get(__package__)
    return item.preferences if item else None


def tag_panels():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in SUPPORTED_EDITORS:
                area.tag_redraw()


class Runtime:
    def __init__(self):
        self.service = None
        self._active = False
        self.window_pointer = None
        self.area_pointer = None
        self._snapshot_area_pointer = None
        self.observers = {}
        self.sequence = 0
        self.last_snapshot = None
        self.error = ""
        self.generation = 0
        self.keyboard = HeldInput()
        self.hud = NativeHUD(self.snapshot_for_area, preferences)

    @property
    def running(self):
        return self._active

    def snapshot_for_area(self, area_pointer):
        if self.area_pointer == area_pointer == self._snapshot_area_pointer:
            return self.last_snapshot
        return None

    def track(self, window, area):
        if window and area and area.type in SUPPORTED_EDITORS:
            # Keep the operation's original editor even when its pointer
            # crosses another editor during a transform.
            if (self.last_snapshot and self.last_snapshot.get('modal')
                    and window.as_pointer() == self.window_pointer):
                return
            self.window_pointer = window.as_pointer()
            self.area_pointer = area.as_pointer()

    def resolve(self):
        windows = list(bpy.context.window_manager.windows)
        ordered = sorted(windows, key=lambda w: w.as_pointer() != self.window_pointer)
        for window in ordered:
            for area in window.screen.areas:
                if area.as_pointer() == self.area_pointer and area.type in SUPPORTED_EDITORS:
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    if region:
                        return window, area, region
        for window in ordered:
            for area in window.screen.areas:
                if area.type in SUPPORTED_EDITORS:
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    if region:
                        self.track(window, area)
                        return window, area, region
        return None

    def refresh(self):
        if not (self.running or self.service):
            return
        target = self.resolve()
        if not target:
            self.last_snapshot = None
            self._snapshot_area_pointer = None
            return
        window, area, region = target
        with bpy.context.temp_override(window=window, area=area, region=region):
            info = engine.gather_context(bpy.context)
            modal = modal_context.active_modal(bpy.context)
            previous_modal = (self.last_snapshot or {}).get('modal')
            if bool(modal) != bool(previous_modal) or (modal and previous_modal and modal['operator'] != previous_modal.get('operator')):
                # Native modal handlers may consume letter releases. The key
                # that started/finished an operation must not remain a prefix.
                self.keyboard.keys.clear()
            if modal:
                bindings = modal['bindings']
            else:
                bindings = prefix.enumerate_bindings(bpy.context) if (self.keyboard.modifiers or self.keyboard.keys) else []
            key_modifiers = {row.get('key_modifier') for row in bindings if row.get('key_modifier') not in {None, '', 'NONE'}}
            input_info = self.keyboard.snapshot(key_modifiers)
            held_modifiers, held_keys = set(input_info['held_modifiers']), set(input_info['held_keys'])
            matches = prefix.filter_bindings(bindings, held_modifiers, held_keys, allow_empty=bool(modal))
            steps = prefix.next_steps(bindings, held_modifiers, held_keys, allow_empty=bool(modal))
            modal_info = {key:value for key,value in modal.items() if key != 'bindings'} if modal else None
            display_mode = 'modal' if modal else ('prefix' if input_info['active'] else 'common')
            common = common_shortcuts(bpy.context, info=info) if display_mode == 'common' else []
            if display_mode == 'common':
                matches = common
            snapshot = {'schema_version': 1, 'context': info, 'input': input_info,
                        'modal': modal_info, 'next_steps': steps,
                        'display_mode': display_mode, 'common_shortcuts': common,
                        'common_count': len(common),
                        'recommendations': matches, 'candidate_count': len(steps),
                        'binding_count': len(matches)}
        self.sequence += 1
        snapshot.update({
            "sequence": self.sequence, "updated_at": time.time(),
            "blender_version": bpy.app.version_string,
            "tracking": "last_hovered_editor", "status": "connected",
        })
        self.last_snapshot = snapshot
        self._snapshot_area_pointer = area.as_pointer()
        if self.service:
            self.service.publish(snapshot)

    def ensure_observers(self):
        for window in bpy.context.window_manager.windows:
            pointer = window.as_pointer()
            if pointer in self.observers:
                continue
            area = next((a for a in window.screen.areas if a.type in SUPPORTED_EDITORS), None)
            if area:
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.shortcut_compass.observe("INVOKE_DEFAULT")

    def start(self, context):
        if self.running:
            return
        if preferences(context) is None:
            raise RuntimeError("请先在偏好设置中启用快捷键助手")
        self._active = True
        self.generation += 1
        self.track(context.window, context.area)
        try:
            self.ensure_observers()
            self.refresh()
            if not bpy.app.timers.is_registered(refresh_timer):
                bpy.app.timers.register(refresh_timer, first_interval=0.25, persistent=True)
        except Exception:
            self.stop()
            raise
        self.error = ""

    def show_hud(self, context):
        self.start(context)
        self.hud.start()
        self.refresh()
        tag_panels()

    def hide_hud(self):
        self.hud.stop()
        if not self.service:
            self.stop()
        tag_panels()

    def start_bridge(self, context):
        if self.service:
            return
        prefs = preferences(context)
        if prefs is None:
            raise RuntimeError("请先启用快捷键助手")
        if not prefs.local_token:
            prefs.local_token = secrets.token_urlsafe(32)
        self.start(context)
        self.service = SnapshotService(prefs.port, prefs.local_token)
        self.refresh()

    def stop(self):
        self._active = False
        self.generation += 1
        self.hud.stop()
        if bpy.app.timers.is_registered(refresh_timer):
            bpy.app.timers.unregister(refresh_timer)
        for observer in list(self.observers.values()):
            observer.cleanup(bpy.context)
        self.observers.clear()
        if self.service:
            self.service.stop()
            self.service = None
        self.last_snapshot = None
        self._snapshot_area_pointer = None
        self.keyboard.clear()
        tag_panels()


runtime = Runtime()


def refresh_timer():
    if not runtime.running:
        return None
    try:
        runtime.ensure_observers()
        # A native modal operator can consume modifier events before our
        # passive observer. Sample only modifier state in this Blender process
        # on Windows; other platforms retain Blender's event-based tracking.
        platform_state = sample_platform_modifiers()
        if platform_state is not None:
            runtime.keyboard.sync_modifiers(platform_state)
        runtime.refresh()
        tag_panels()
    except Exception as exc:
        runtime.error = str(exc)
    return 0.12


def preferences_changed(_self, _context):
    tag_panels()


class SHORTCUTCOMPASS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_on_startup: BoolProperty(name="启动时显示悬浮提示", default=True)
    font_size: IntProperty(name="提示字号", default=12, min=9, max=20, update=preferences_changed)
    hud_opacity: FloatProperty(name="背景不透明度", default=0.82, min=0.2, max=1.0, update=preferences_changed)
    hud_x: IntProperty(name="水平位置", default=12, min=0, max=10000)
    hud_y: IntProperty(name="垂直位置", default=12, min=0, max=10000)
    port: IntProperty(name="本机连接端口", default=18764, min=1024, max=65535)
    local_token: StringProperty(name="本机连接令牌", subtype="PASSWORD", default="")

    def draw(self, context):
        layout = self.layout
        layout.label(text="常驻显示常用快捷键；按住 Ctrl / Shift / Alt 查看下一步")
        layout.prop(self, "show_on_startup")
        layout.prop(self, "font_size")
        layout.prop(self, "hud_opacity")
        layout.label(text="拖动左侧手柄调整位置；右侧按钮收起 / 展开")
        draw_controls(layout, context)
        box = layout.box()
        box.label(text="可选 MCP 数据桥")
        box.prop(self, "port")
        box.prop(self, "local_token")
        box.operator("shortcut_compass.copy_token", icon="COPYDOWN")


class SHORTCUTCOMPASS_OT_Observe(bpy.types.Operator):
    bl_idname = "shortcut_compass.observe"
    bl_label = "快捷键助手：跟踪上下文"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        pointer = context.window.as_pointer()
        if pointer in runtime.observers:
            return {"CANCELLED"}
        self._window = context.window
        self._pointer = pointer
        self._generation = runtime.generation
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        runtime.observers[pointer] = self
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def cleanup(self, context):
        if getattr(self, "_timer", None):
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError):
                pass
            self._timer = None
        if runtime.observers.get(self._pointer) == self:
            runtime.observers.pop(self._pointer, None)

    def modal(self, context, event):
        if not runtime.running or self._generation != runtime.generation:
            self.cleanup(context)
            return {"CANCELLED"}
        input_changed = runtime.keyboard.update(event)
        if input_changed:
            try:
                runtime.refresh()
            except Exception as exc:
                # A third-party operator poll must not terminate the passive
                # observer or prevent Blender from receiving its shortcut.
                runtime.error = str(exc)
            tag_panels()
        # A grip drag keeps its original editor even when the pointer crosses
        # another area, a header, or the edge of the window. Always deliver the
        # matching release, rather than leaving an unfinished drag latched.
        if runtime.hud._drag is not None:
            with context.temp_override(window=self._window):
                handled = runtime.hud.handle_event(bpy.context, event)
            if handled:
                tag_panels()
            return {"RUNNING_MODAL"} if handled else {"PASS_THROUGH"}
        if runtime.hud._click_capture is not None and event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                runtime.hud._click_capture = None
            else:
                with context.temp_override(window=self._window):
                    handled = runtime.hud.handle_event(bpy.context, event)
                if handled:
                    return {"RUNNING_MODAL"}
        if event.type not in {"TIMER", "TIMER_REPORT", "TIMER_JOBS", "WINDOW_DEACTIVATE"}:
            try:
                for area in self._window.screen.areas:
                    if (area.x <= event.mouse_x < area.x + area.width
                            and area.y <= event.mouse_y < area.y + area.height):
                        runtime.track(self._window, area)
                        region = next((r for r in area.regions if r.type == "WINDOW"), None)
                        if region and area.type in SUPPORTED_EDITORS:
                            with context.temp_override(window=self._window, area=area, region=region):
                                handled = runtime.hud.handle_event(bpy.context, event)
                            if handled:
                                area.tag_redraw()
                                return {"RUNNING_MODAL"}
                        break
            except ReferenceError:
                self.cleanup(context)
                return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        self.cleanup(context)


class SHORTCUTCOMPASS_OT_ShowHUD(bpy.types.Operator):
    bl_idname = "shortcut_compass.show_hud"
    bl_label = "快捷键助手：显示悬浮提示"
    bl_description = "常驻显示常用快捷键，按住前缀或操作时显示下一步按键"

    def execute(self, context):
        try:
            runtime.show_hud(context)
        except Exception as exc:
            runtime.error = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class SHORTCUTCOMPASS_OT_HideHUD(bpy.types.Operator):
    bl_idname = "shortcut_compass.hide_hud"
    bl_label = "快捷键助手：隐藏悬浮提示"

    def execute(self, context):
        runtime.hide_hud()
        return {"FINISHED"}


class SHORTCUTCOMPASS_OT_StartBridge(bpy.types.Operator):
    bl_idname = "shortcut_compass.start_bridge"
    bl_label = "快捷键助手：启动本机 MCP 数据桥"

    def execute(self, context):
        try:
            runtime.start_bridge(context)
            tag_panels()
        except Exception as exc:
            runtime.error = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class SHORTCUTCOMPASS_OT_Stop(bpy.types.Operator):
    bl_idname = "shortcut_compass.stop"
    bl_label = "快捷键助手：停止"

    def execute(self, context):
        runtime.stop()
        return {"FINISHED"}


class SHORTCUTCOMPASS_OT_CopyToken(bpy.types.Operator):
    bl_idname = "shortcut_compass.copy_token"
    bl_label = "复制 MCP 连接令牌"

    def execute(self, context):
        prefs = preferences(context)
        if not prefs.local_token:
            prefs.local_token = secrets.token_urlsafe(32)
        context.window_manager.clipboard = prefs.local_token
        self.report({"INFO"}, "MCP 连接令牌已复制")
        return {"FINISHED"}


def draw_controls(layout, context):
    if runtime.hud.enabled:
        layout.operator("shortcut_compass.hide_hud", icon="HIDE_ON")
    else:
        layout.operator("shortcut_compass.show_hud", icon="HIDE_OFF")
    prefs = preferences(context)
    if prefs:
        layout.prop(prefs, "font_size")
    if runtime.service:
        layout.label(text=f"MCP 数据桥已启动 · {runtime.service.port}", icon="LINKED")
    else:
        layout.operator("shortcut_compass.start_bridge", icon="LINKED")
    if runtime.running:
        layout.operator("shortcut_compass.stop", icon="CANCEL")
    error = runtime.error or runtime.hud.error
    if error:
        box = layout.box()
        box.alert = True
        box.label(text=error[:90], icon="ERROR")
    if runtime.last_snapshot:
        box = layout.box()
        input_info = runtime.last_snapshot.get('input', {})
        modal_info = runtime.last_snapshot.get('modal')
        title = (modal_info.get('label_zh') or modal_info.get('operator')) if modal_info else input_info.get('prefix_label', '')
        box.label(text=(title + ' → 下一步按键') if title else '当前常用快捷键')
        items = (runtime.last_snapshot.get('common_shortcuts', [])
                 if runtime.last_snapshot.get('display_mode') == 'common'
                 else runtime.last_snapshot.get('next_steps', []))
        for item in items:
            row = box.row()
            row.label(text=item.get("label_zh", ""))
            row.label(text=item.get("shortcut") if item.get("bound") else "未绑定")


_panel_classes = []
for _space in ("VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR", "GRAPH_EDITOR", "DOPESHEET_EDITOR"):
    _panel_classes.append(type(
        "SHORTCUTCOMPASS_PT_" + _space,
        (bpy.types.Panel,),
        {
            "bl_label": "快捷键助手", "bl_space_type": _space,
            "bl_region_type": "UI", "bl_category": "快捷键",
            "draw": lambda self, context: draw_controls(self.layout, context),
        },
    ))


def window_menu(self, context):
    self.layout.separator()
    self.layout.operator("shortcut_compass.hide_hud" if runtime.hud.enabled else "shortcut_compass.show_hud", icon="INFO")


def startup_hud():
    prefs = preferences()
    if prefs and prefs.show_on_startup and bpy.context.window_manager.windows:
        try:
            window = bpy.context.window_manager.windows[0]
            area = next((a for a in window.screen.areas if a.type in SUPPORTED_EDITORS), None)
            if area:
                with bpy.context.temp_override(window=window, area=area):
                    runtime.show_hud(bpy.context)
        except Exception as exc:
            runtime.error = str(exc)
    return None


@persistent
def on_load(_dummy):
    runtime.window_pointer = None
    runtime.area_pointer = None
    runtime._snapshot_area_pointer = None
    runtime.observers.clear()
    runtime.generation += 1
    runtime.keyboard.clear()
    if not runtime.running and not bpy.app.timers.is_registered(startup_hud):
        bpy.app.timers.register(startup_hud, first_interval=0.5)


_classes = (
    SHORTCUTCOMPASS_Preferences, SHORTCUTCOMPASS_OT_Observe,
    SHORTCUTCOMPASS_OT_ShowHUD, SHORTCUTCOMPASS_OT_HideHUD,
    SHORTCUTCOMPASS_OT_StartBridge, SHORTCUTCOMPASS_OT_Stop,
    SHORTCUTCOMPASS_OT_CopyToken, *_panel_classes,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_window.append(window_menu)
    bpy.app.handlers.load_post.append(on_load)
    if not bpy.app.background:
        bpy.app.timers.register(startup_hud, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(startup_hud):
        bpy.app.timers.unregister(startup_hud)
    runtime.stop()
    bpy.types.TOPBAR_MT_window.remove(window_menu)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
