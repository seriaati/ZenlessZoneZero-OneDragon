"""Linux 下的全局键鼠监听。

KDE Wayland 等 Wayland 桌面上 pynput 只能收到 XWayland 窗口的按键 不能用作全局快捷键。
这里直接读取 /dev/input 下的键盘鼠标设备 (需要当前用户在 input 组里)。
只读取事件 不独占设备 不影响桌面和游戏的正常输入。

按键名称与 Windows 下 pynput 监听得到的名称一致 (f9 / a / 1 / numpad_1 / ctrl_l / esc ...)。
字符键按物理位置对应美式键盘布局。
"""

from __future__ import annotations

import contextlib
import os
import select
import threading
from collections.abc import Callable

import evdev
from evdev import ecodes
from pynput import keyboard

from one_dragon.utils.log_utils import log

# evdev 键码 → 按键名称 (与 Windows 下 PcButtonListener 得到的名称一致)
_KEY_NAMES: dict[int, str] = {
    ecodes.KEY_ESC: 'esc',
    ecodes.KEY_ENTER: 'enter',
    ecodes.KEY_KPENTER: 'enter',
    ecodes.KEY_SPACE: 'space',
    ecodes.KEY_TAB: 'tab',
    ecodes.KEY_BACKSPACE: 'backspace',
    ecodes.KEY_CAPSLOCK: 'caps_lock',
    ecodes.KEY_LEFTSHIFT: 'shift',
    ecodes.KEY_RIGHTSHIFT: 'shift_r',
    ecodes.KEY_LEFTCTRL: 'ctrl_l',
    ecodes.KEY_RIGHTCTRL: 'ctrl_r',
    ecodes.KEY_LEFTALT: 'alt_l',
    ecodes.KEY_RIGHTALT: 'alt_r',
    ecodes.KEY_LEFTMETA: 'cmd',
    ecodes.KEY_RIGHTMETA: 'cmd_r',
    ecodes.KEY_COMPOSE: 'menu',
    ecodes.KEY_INSERT: 'insert',
    ecodes.KEY_DELETE: 'delete',
    ecodes.KEY_HOME: 'home',
    ecodes.KEY_END: 'end',
    ecodes.KEY_PAGEUP: 'page_up',
    ecodes.KEY_PAGEDOWN: 'page_down',
    ecodes.KEY_UP: 'up',
    ecodes.KEY_DOWN: 'down',
    ecodes.KEY_LEFT: 'left',
    ecodes.KEY_RIGHT: 'right',
    ecodes.KEY_SYSRQ: 'print_screen',
    ecodes.KEY_SCROLLLOCK: 'scroll_lock',
    ecodes.KEY_PAUSE: 'pause',
    ecodes.KEY_NUMLOCK: 'num_lock',
    ecodes.KEY_PLAYPAUSE: 'media_play_pause',
    ecodes.KEY_STOPCD: 'media_stop',
    ecodes.KEY_MUTE: 'media_volume_mute',
    ecodes.KEY_VOLUMEDOWN: 'media_volume_down',
    ecodes.KEY_VOLUMEUP: 'media_volume_up',
    ecodes.KEY_PREVIOUSSONG: 'media_previous',
    ecodes.KEY_NEXTSONG: 'media_next',
    ecodes.KEY_MINUS: '-',
    ecodes.KEY_EQUAL: '=',
    ecodes.KEY_LEFTBRACE: '[',
    ecodes.KEY_RIGHTBRACE: ']',
    ecodes.KEY_SEMICOLON: ';',
    ecodes.KEY_APOSTROPHE: "'",
    ecodes.KEY_GRAVE: '`',
    ecodes.KEY_BACKSLASH: '\\',
    ecodes.KEY_COMMA: ',',
    ecodes.KEY_DOT: '.',
    ecodes.KEY_SLASH: '/',
    # 小键盘的运算键 Windows 下没有字符 名称是虚拟键码
    ecodes.KEY_KPASTERISK: 'vk_106',
    ecodes.KEY_KPPLUS: 'vk_107',
    ecodes.KEY_KPMINUS: 'vk_109',
    ecodes.KEY_KPDOT: 'vk_110',
    ecodes.KEY_KPSLASH: 'vk_111',
}
for _i in range(26):
    _KEY_NAMES[ecodes.ecodes[f'KEY_{chr(ord("A") + _i)}']] = chr(ord('a') + _i)
for _i in range(10):
    _KEY_NAMES[ecodes.ecodes[f'KEY_{_i}']] = str(_i)
    _KEY_NAMES[ecodes.ecodes[f'KEY_KP{_i}']] = f'numpad_{_i}'
for _i in range(1, 25):
    _KEY_NAMES[ecodes.ecodes[f'KEY_F{_i}']] = f'f{_i}'

# evdev 鼠标键码 → 鼠标按键名称 (不含 mouse_ 前缀)
_MOUSE_NAMES: dict[int, str] = {
    ecodes.BTN_LEFT: 'left',
    ecodes.BTN_RIGHT: 'right',
    ecodes.BTN_MIDDLE: 'middle',
    ecodes.BTN_SIDE: 'x1',
    ecodes.BTN_EXTRA: 'x2',
}

# 没有找到新设备时 隔多久重新扫描一次 /dev/input (秒) 用于发现后插入的设备
_RESCAN_SECONDS: float = 2.0


class EvdevButtonListener:
    """读取 /dev/input 的键盘鼠标事件 用名称回调。

    on_key_press 收到长按时的重复事件 与 pynput 一致。
    只传入键盘回调时只打开键盘设备 只传入鼠标回调时只打开鼠标设备。
    """

    def __init__(
            self,
            on_key_press: Callable[[str], None] | None = None,
            on_key_release: Callable[[str], None] | None = None,
            on_mouse_press: Callable[[str], None] | None = None,
    ) -> None:
        self.on_key_press: Callable[[str], None] | None = on_key_press
        self.on_key_release: Callable[[str], None] | None = on_key_release
        self.on_mouse_press: Callable[[str], None] | None = on_mouse_press

        self._wanted_codes: set[int] = set()
        if on_key_press is not None or on_key_release is not None:
            self._wanted_codes.update(_KEY_NAMES.keys())
        if on_mouse_press is not None:
            self._wanted_codes.update(_MOUSE_NAMES.keys())

        self._devices: dict[str, evdev.InputDevice] = {}  # 设备路径 → 设备
        self._skipped_paths: set[str] = set()  # 不需要的设备 不再重复打开
        self._permission_warned: bool = False
        self._thread: threading.Thread | None = None
        self._stop_r: int = -1  # 用于唤醒监听线程的管道 启动时创建
        self._stop_w: int = -1
        self._lock: threading.Lock = threading.Lock()
        self._stopped: bool = False
        self._closed: bool = False  # 监听线程已退出 管道已关闭

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopped:
                return
            self._stop_r, self._stop_w = os.pipe()
            self._thread = threading.Thread(target=self._run, name='od_evdev_listener', daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """停止监听 可以重复调用 没有启动时也可以调用"""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            if self._thread is not None and not self._closed:
                os.write(self._stop_w, b'x')  # 唤醒监听线程 由它关闭设备

    def _scan_devices(self) -> None:
        """打开新出现的、带有需要按键的设备"""
        paths = evdev.list_devices()
        self._skipped_paths.intersection_update(paths)  # 已拔出的设备 路径可能被新设备复用
        for path in paths:
            if path in self._devices or path in self._skipped_paths:
                continue
            try:
                device = evdev.InputDevice(path)
            except PermissionError:
                self._skipped_paths.add(path)
                if not self._permission_warned:
                    self._permission_warned = True
                    log.warning('没有权限读取 /dev/input 全局快捷键不可用 请把当前用户加入 input 组后重新登录')
                continue
            except OSError:
                continue  # 设备刚好被拔出

            key_codes = device.capabilities().get(ecodes.EV_KEY, [])
            if self._wanted_codes.isdisjoint(key_codes):
                device.close()
                self._skipped_paths.add(path)
                continue
            self._devices[path] = device
            log.debug(f'按键监听 打开设备 {path} {device.name}')

    def _close_device(self, path: str) -> None:
        device = self._devices.pop(path, None)
        if device is not None:
            with contextlib.suppress(OSError):
                device.close()

    def _close_all(self) -> None:
        """监听线程退出时调用 关闭设备和管道"""
        with self._lock:
            self._closed = True
            os.close(self._stop_r)
            os.close(self._stop_w)
        for path in list(self._devices.keys()):
            self._close_device(path)

    def _run(self) -> None:
        try:
            while not self._stopped:
                self._scan_devices()
                fd_to_path = {device.fd: path for path, device in self._devices.items()}
                readable, _, _ = select.select([self._stop_r, *fd_to_path.keys()], [], [], _RESCAN_SECONDS)
                for fd in readable:
                    if fd == self._stop_r or self._stopped:
                        continue
                    path = fd_to_path[fd]
                    try:
                        events = list(self._devices[path].read())
                    except BlockingIOError:
                        continue
                    except OSError:  # 设备被拔出
                        self._close_device(path)
                        continue
                    for event in events:
                        if event.type == ecodes.EV_KEY:
                            self._on_key_event(event.code, event.value)
        except Exception:
            log.error('按键监听出错', exc_info=True)
        finally:
            self._close_all()

    def _on_key_event(self, code: int, value: int) -> None:
        """
        :param code: evdev 键码
        :param value: 1=按下 0=松开 2=长按重复
        """
        key_name = _KEY_NAMES.get(code)
        if key_name is not None:
            if value in (1, 2) and self.on_key_press is not None:
                self._safe_call(self.on_key_press, key_name)
            elif value == 0 and self.on_key_release is not None:
                self._safe_call(self.on_key_release, key_name)
            return

        mouse_name = _MOUSE_NAMES.get(code)
        if mouse_name is not None and value == 1 and self.on_mouse_press is not None:
            self._safe_call(self.on_mouse_press, mouse_name)

    @staticmethod
    def _safe_call(callback: Callable[[str], None], name: str) -> None:
        try:
            callback(name)
        except Exception:
            log.error('按键回调出错', exc_info=True)


def to_pynput_key(name: str) -> keyboard.Key | keyboard.KeyCode | None:
    """按键名称 → pynput 按键对象 字母数字和小键盘数字使用 Windows 的虚拟键码"""
    if name in keyboard.Key.__members__:
        return keyboard.Key[name]
    if len(name) == 1:
        vk = ord(name.upper()) if name.isalnum() else None
        return keyboard.KeyCode(vk=vk, char=name)
    if name.startswith('numpad_'):
        return keyboard.KeyCode(vk=0x60 + int(name[-1]))
    if name.startswith('vk_'):
        return keyboard.KeyCode(vk=int(name[3:]))
    return None


class EvdevKeyboardListener:
    """与 pynput.keyboard.Listener 用法相同的键盘监听 回调收到 pynput 的按键对象"""

    def __init__(
            self,
            on_press: Callable[[keyboard.Key | keyboard.KeyCode | None], None] | None = None,
            on_release: Callable[[keyboard.Key | keyboard.KeyCode | None], None] | None = None,
    ) -> None:
        self._on_press: Callable[[keyboard.Key | keyboard.KeyCode | None], None] | None = on_press
        self._on_release: Callable[[keyboard.Key | keyboard.KeyCode | None], None] | None = on_release
        self._listener: EvdevButtonListener = EvdevButtonListener(
            on_key_press=self._handle_press,
            on_key_release=self._handle_release,
        )

    def _handle_press(self, name: str) -> None:
        key = to_pynput_key(name)
        if key is not None and self._on_press is not None:
            self._on_press(key)

    def _handle_release(self, name: str) -> None:
        key = to_pynput_key(name)
        if key is not None and self._on_release is not None:
            self._on_release(key)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
