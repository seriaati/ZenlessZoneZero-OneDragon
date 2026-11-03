"""Linux 下用 XTest 向 gamescope 里的游戏发送键鼠输入。

输入只发到游戏所在的 X 显示 不会影响宿主桌面 游戏没有焦点时也能用。
按键名称与 Windows 下一致 (pynput 的按键名 / 单个字符 / numpad_0~9 / mouse_ 开头的鼠标键)。
"""

from __future__ import annotations

import threading
import time

from pynput import keyboard
from Xlib import XK, X
from Xlib.ext import xtest

from one_dragon.base.controller.gamescope_display import X_ERRORS, GamescopeDisplay
from one_dragon.base.controller.pc_button import pc_button_utils
from one_dragon.base.controller.pc_button.keyboard_mouse_controller import (
    KeyboardMouseController,
)
from one_dragon.base.controller.pc_button.pc_button_controller import PcButtonController
from one_dragon.base.geometry.point import Point
from one_dragon.utils.log_utils import log

# pynput 按键名 → X keysym 名
_KEY_TO_KEYSYM: dict[str, str] = {
    'alt': 'Alt_L',
    'alt_l': 'Alt_L',
    'alt_r': 'Alt_R',
    'alt_gr': 'ISO_Level3_Shift',
    'backspace': 'BackSpace',
    'caps_lock': 'Caps_Lock',
    'cmd': 'Super_L',
    'cmd_l': 'Super_L',
    'cmd_r': 'Super_R',
    'ctrl': 'Control_L',
    'ctrl_l': 'Control_L',
    'ctrl_r': 'Control_R',
    'delete': 'Delete',
    'down': 'Down',
    'end': 'End',
    'enter': 'Return',
    'esc': 'Escape',
    'home': 'Home',
    'left': 'Left',
    'page_down': 'Next',
    'page_up': 'Prior',
    'right': 'Right',
    'shift': 'Shift_L',
    'shift_l': 'Shift_L',
    'shift_r': 'Shift_R',
    'space': 'space',
    'tab': 'Tab',
    'up': 'Up',
    'media_play_pause': 'XF86AudioPlay',
    'media_volume_mute': 'XF86AudioMute',
    'media_volume_down': 'XF86AudioLowerVolume',
    'media_volume_up': 'XF86AudioRaiseVolume',
    'media_previous': 'XF86AudioPrev',
    'media_next': 'XF86AudioNext',
    'insert': 'Insert',
    'menu': 'Menu',
    'num_lock': 'Num_Lock',
    'pause': 'Pause',
    'print_screen': 'Print',
    'scroll_lock': 'Scroll_Lock',
}

# 鼠标键名 (mouse_ 之后的部分) → X 鼠标按钮编号
_MOUSE_BUTTONS: dict[str, int] = {
    'left': 1,
    'middle': 2,
    'right': 3,
    'scroll_up': 4,
    'scroll_down': 5,
    'scroll_left': 6,
    'scroll_right': 7,
    'x1': 8,
    'x2': 9,
}

# XTest 相对移动每个事件的最大距离 (协议里是 INT16)
_MAX_RELATIVE_STEP: int = 32767

# 按下和松开之间的最短间隔 秒
# 实测按下后立刻松开(同一毫秒)游戏收不到 间隔 5ms 就能稳定识别 这里留一倍余量
TAP_HOLD_SECONDS: float = 0.01


def get_keysym(key: str) -> int:
    """按键名转换成 X keysym

    Args:
        key: 按键名 例如 'a' / 'shift' / 'f1' / 'numpad_1'

    Returns:
        keysym 无法识别时返回 0
    """
    if key.startswith('numpad_') and len(key) == 8 and key[-1].isdigit():
        return XK.string_to_keysym(f'KP_{key[-1]}')
    if key in _KEY_TO_KEYSYM:
        return XK.string_to_keysym(_KEY_TO_KEYSYM[key])
    if len(key) > 1 and key[0] == 'f' and key[1:].isdigit():  # f1 ~ f20
        return XK.string_to_keysym(f'F{key[1:]}')
    if len(key) == 1:
        code = ord(key)
        # Latin-1 字符的 keysym 就是字符编码 其他 Unicode 字符是 0x01000000 + 编码
        return code if code < 0x100 else 0x01000000 + code
    return XK.string_to_keysym(key)  # 直接写 X keysym 名的情况


def get_mouse_button(key: str) -> int:
    """鼠标键名转换成 X 鼠标按钮编号

    Args:
        key: 鼠标键名 例如 'mouse_left' / 'mouse_x1' / 'mouse_button8'

    Returns:
        按钮编号 无法识别时返回 0
    """
    name = key[6:]  # 取 mouse_ 之后的部分
    if name in _MOUSE_BUTTONS:
        return _MOUSE_BUTTONS[name]
    if name.startswith('button') and name[6:].isdigit():
        return int(name[6:])
    return 0


class XTestKeyboard:
    """
    模拟 pynput 的 keyboard.Controller 接口
    部分业务代码会直接调用 keyboard_controller.keyboard.type() 等方法
    """

    def __init__(self, controller: XTestKeyboardMouseController):
        self.controller: XTestKeyboardMouseController = controller

    @staticmethod
    def _key_name(key: str | keyboard.Key | keyboard.KeyCode) -> str:
        if isinstance(key, keyboard.Key):
            return key.name
        if isinstance(key, keyboard.KeyCode):
            return key.char if key.char is not None else f'vk_{key.vk}'
        return key

    def press(self, key: str | keyboard.Key | keyboard.KeyCode) -> None:
        self.controller.key_down(self._key_name(key))

    def release(self, key: str | keyboard.Key | keyboard.KeyCode) -> None:
        self.controller.key_up(self._key_name(key))

    def tap(self, key: str | keyboard.Key | keyboard.KeyCode) -> None:
        self.controller.tap(self._key_name(key))

    def type(self, text: str) -> None:
        self.controller.type_text(text)


class XTestKeyboardMouseController(KeyboardMouseController):
    """
    通过 XTest 向 gamescope 显示发送键鼠输入的按键控制器
    """

    def __init__(self, gamescope: GamescopeDisplay):
        # 不调用 KeyboardMouseController.__init__ 避免创建连接宿主桌面的 pynput 控制器
        PcButtonController.__init__(self)
        self.gamescope: GamescopeDisplay = gamescope
        self.keyboard: XTestKeyboard = XTestKeyboard(self)
        self._pressed_keys: set[str] = set()  # 当前按下的键
        self._lock: threading.RLock = threading.RLock()

    def _fake_input(self, event_type: int, detail: int = 0, x: int = 0, y: int = 0) -> bool:
        """发送一个 XTest 事件

        Args:
            event_type: X 事件类型
            detail: 键码 / 鼠标按钮 / 相对移动标记
            x: 横坐标
            y: 纵坐标

        Returns:
            是否发送成功
        """
        d = self.gamescope.get_display()
        if d is None:
            log.error('未找到游戏显示 无法发送输入')
            return False
        try:
            if event_type == X.MotionNotify:
                xtest.fake_input(d, event_type, detail=detail, root=d.screen().root, x=x, y=y)
            else:
                xtest.fake_input(d, event_type, detail=detail)
            d.flush()
            return True
        except X_ERRORS:
            log.error('发送输入失败', exc_info=True)
            self.gamescope.on_x_failure()
            return False

    def _get_keycode(self, key: str) -> int:
        """按键名转换成游戏显示上的键码 找不到时返回 0"""
        d = self.gamescope.get_display()
        if d is None:
            return 0
        keysym = get_keysym(key)
        keycode = d.keysym_to_keycode(keysym) if keysym else 0
        if keycode == 0:
            log.error(f'无法识别的按键 {key}')
        return keycode

    def key_down(self, key: str) -> None:
        """按下键盘或鼠标键 (不记录状态)"""
        if pc_button_utils.is_mouse_button(key):
            button = get_mouse_button(key)
            if button == 0:
                log.error(f'无法识别的鼠标键 {key}')
                return
            self._fake_input(X.ButtonPress, button)
        else:
            keycode = self._get_keycode(key)
            if keycode != 0:
                self._fake_input(X.KeyPress, keycode)

    def key_up(self, key: str) -> None:
        """松开键盘或鼠标键 (不记录状态)"""
        if pc_button_utils.is_mouse_button(key):
            button = get_mouse_button(key)
            if button != 0:
                self._fake_input(X.ButtonRelease, button)
        else:
            keycode = self._get_keycode(key)
            if keycode != 0:
                self._fake_input(X.KeyRelease, keycode)

    def tap(self, key: str) -> None:
        """
        按一次按键
        :param key: 按键
        """
        self.key_down(key)
        time.sleep(TAP_HOLD_SECONDS)
        self.key_up(key)

    def press(self, key: str, press_time: float | None = None) -> None:
        """
        :param key: 按键
        :param press_time: 持续按键时间 不传入时代表不松开
        """
        self.key_down(key)
        if press_time is None:
            self._pressed_keys.add(key)
            return
        time.sleep(max(TAP_HOLD_SECONDS, press_time))
        self.key_up(key)

    def release(self, key: str) -> None:
        if key not in self._pressed_keys:
            return
        self._pressed_keys.discard(key)
        self.key_up(key)

    def reset(self) -> None:
        for key in list(self._pressed_keys):
            self.release(key)

    def move_to(self, pos: Point) -> bool:
        """鼠标移动到显示上的绝对坐标"""
        return self._fake_input(X.MotionNotify, 0, int(pos.x), int(pos.y))

    def move_relative(self, dx: int, dy: int) -> None:
        """鼠标相对移动 游戏会当作原始鼠标移动处理 (用于转动视角)"""
        while dx != 0 or dy != 0:
            step_x = max(-_MAX_RELATIVE_STEP, min(_MAX_RELATIVE_STEP, dx))
            step_y = max(-_MAX_RELATIVE_STEP, min(_MAX_RELATIVE_STEP, dy))
            if not self._fake_input(X.MotionNotify, 1, step_x, step_y):
                return
            dx -= step_x
            dy -= step_y

    def mouse_down(self, button: int = 1) -> None:
        self._fake_input(X.ButtonPress, button)

    def mouse_up(self, button: int = 1) -> None:
        self._fake_input(X.ButtonRelease, button)

    def scroll(self, clicks: int) -> None:
        """滚动鼠标滚轮

        Args:
            clicks: 滚动格数 正数向下 负数向上
        """
        button = 5 if clicks > 0 else 4
        for _ in range(abs(clicks)):
            self._fake_input(X.ButtonPress, button)
            time.sleep(TAP_HOLD_SECONDS)
            self._fake_input(X.ButtonRelease, button)

    def get_position(self) -> Point | None:
        """获取鼠标在游戏显示上的位置"""
        d = self.gamescope.get_display()
        if d is None:
            return None
        try:
            pointer = d.screen().root.query_pointer()
        except X_ERRORS:
            self.gamescope.on_x_failure()
            return None
        return Point(pointer.root_x, pointer.root_y)

    def type_text(self, text: str, interval: float = 0.01) -> None:
        """逐个字符输入文本 需要按 Shift 的字符会自动按住 Shift

        Args:
            text: 文本
            interval: 每个字符之间的间隔 秒
        """
        d = self.gamescope.get_display()
        if d is None:
            log.error('未找到游戏显示 无法输入文本')
            return
        shift_keycode = d.keysym_to_keycode(XK.string_to_keysym('Shift_L'))
        with self._lock:
            for ch in text:
                keysym = XK.string_to_keysym('Return') if ch == '\n' else get_keysym(ch)
                # 选择不需要额外修饰键的位置: 0=直接按 1=按住 Shift
                found = [(index, keycode) for keycode, index in d.keysym_to_keycodes(keysym) if index in (0, 1)]
                if len(found) == 0:
                    log.error('当前键盘布局无法输入字符 已跳过')
                    continue
                index, keycode = min(found)
                if index == 1:
                    self._fake_input(X.KeyPress, shift_keycode)
                self._fake_input(X.KeyPress, keycode)
                time.sleep(TAP_HOLD_SECONDS)
                self._fake_input(X.KeyRelease, keycode)
                if index == 1:
                    self._fake_input(X.KeyRelease, shift_keycode)
                if interval > 0:
                    time.sleep(interval)
