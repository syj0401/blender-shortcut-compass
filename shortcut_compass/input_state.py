"""Track held keys from Blender events without capturing native shortcuts."""

import os
import sys

MODIFIER_ORDER = ('ctrl', 'shift', 'alt', 'oskey', 'hyper')
MODIFIER_LABELS = {'ctrl': 'Ctrl', 'shift': 'Shift', 'alt': 'Alt', 'oskey': 'Win / Cmd', 'hyper': 'Hyper'}
MODIFIER_EVENTS = {
    'LEFT_CTRL': 'ctrl', 'RIGHT_CTRL': 'ctrl',
    'LEFT_SHIFT': 'shift', 'RIGHT_SHIFT': 'shift',
    'LEFT_ALT': 'alt', 'RIGHT_ALT': 'alt',
    'OSKEY': 'oskey', 'HYPER': 'hyper',
}

_native_input = None


def sample_platform_modifiers():
    """Read modifiers only when this Blender process owns Windows' focus.

    Modal operators may swallow PRESS/RELEASE before our observer sees them.
    None requests the portable event-based fallback, not an empty key state.
    """
    global _native_input
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        if _native_input is None:
            native = ctypes.WinDLL('user32', use_last_error=True)
            native.GetForegroundWindow.restype = wintypes.HWND
            native.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            native.GetAsyncKeyState.argtypes = [ctypes.c_int]
            native.GetAsyncKeyState.restype = ctypes.c_short
            _native_input = native
        native = _native_input
        foreground = native.GetForegroundWindow()
        pid = wintypes.DWORD()
        native.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        if pid.value != os.getpid():
            return set()
        keys = {'ctrl':(0x11,), 'shift':(0x10,), 'alt':(0x12,), 'oskey':(0x5B,0x5C)}
        return {name for name,codes in keys.items() if any(native.GetAsyncKeyState(code) < 0 for code in codes)}
    except (AttributeError, OSError):
        return None


def is_keyboard_key(key):
    if key in MODIFIER_EVENTS:
        return False
    if len(key) == 1 and key.isalpha():
        return True
    if key.startswith(('NUMPAD_', 'MEDIA_')) or (key.startswith('F') and key[1:].isdigit()):
        return True
    return key in {
        'ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
        'SPACE', 'TAB', 'RET', 'ESC', 'BACK_SPACE', 'DEL', 'SEMI_COLON', 'PERIOD', 'COMMA',
        'QUOTE', 'ACCENT_GRAVE', 'MINUS', 'SLASH', 'BACK_SLASH', 'EQUAL', 'LEFT_BRACKET',
        'RIGHT_BRACKET', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'PAUSE',
        'INSERT', 'HOME', 'PAGE_UP', 'PAGE_DOWN', 'END', 'GRLESS',
    }


class HeldInput:
    def __init__(self):
        self.modifiers = set()
        self.keys = set()
        self._modifier_sides = set()

    def clear(self):
        changed = bool(self.modifiers or self.keys or self._modifier_sides)
        self.modifiers.clear()
        self.keys.clear()
        self._modifier_sides.clear()
        return changed

    def sync_modifiers(self, modifiers):
        current = set(modifiers).intersection(MODIFIER_ORDER)
        changed = current != self.modifiers
        self.modifiers = current
        self._modifier_sides = {key for key in self._modifier_sides if MODIFIER_EVENTS[key] in current}
        return changed

    def update(self, event):
        event_type = event.type
        if event_type == 'WINDOW_DEACTIVATE':
            return self.clear()
        if event_type.startswith('TIMER'):
            return False
        previous = (frozenset(self.modifiers), frozenset(self.keys))
        modifiers = {name for name in MODIFIER_ORDER if bool(getattr(event, name, False))}
        mapped = MODIFIER_EVENTS.get(event_type)
        if mapped:
            if event.value == 'PRESS':
                self._modifier_sides.add(event_type)
                modifiers.add(mapped)
            elif event.value == 'RELEASE':
                self._modifier_sides.discard(event_type)
                other_side = any(MODIFIER_EVENTS[key] == mapped for key in self._modifier_sides)
                if other_side:
                    modifiers.add(mapped)
                else:
                    modifiers.discard(mapped)
        self.modifiers = modifiers
        if is_keyboard_key(event_type):
            if event.value == 'PRESS':
                self.keys.add(event_type)
            elif event.value == 'RELEASE':
                self.keys.discard(event_type)
        return previous != (frozenset(self.modifiers), frozenset(self.keys))

    def snapshot(self, key_modifiers=()):
        # Ordinary shortcut terminal keys aren't prefixes. Only include an
        # extra held letter when an actual keymap uses it as key_modifier.
        keys = sorted(self.keys.intersection(key_modifiers))
        modifiers = [name for name in MODIFIER_ORDER if name in self.modifiers]
        label = ' + '.join([MODIFIER_LABELS[name] for name in modifiers] + keys)
        return {'held_modifiers': modifiers, 'held_keys': keys,
                'prefix_label': label, 'active': bool(modifiers or keys)}
