"""Linux 下与 gamescope 里游戏的 X 显示连接。

游戏运行在 gamescope 里，gamescope 自带一个独立的 Xwayland 显示(例如 `:1`)。
截图和键鼠输入都直接发到这个显示上，不会影响宿主桌面，所以天然就是"后台"。

查找流程:
1. 在 /proc 里找到命令行第一个参数是 `ZenlessZoneZero.exe` 的进程;
2. 读取该进程环境变量里的 DISPLAY;
3. 用 python-xlib 连接这个显示(游戏常在 flatpak 沙盒里, 显示的套接字可能只在沙盒的 /tmp 或抽象命名空间里);
4. 在根窗口下找到游戏窗口(优先 gamescope 的 `GAMESCOPE_FOCUSED_WINDOW`, 再按窗口标题查找)。
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import socket
import threading
from pathlib import Path

from Xlib import X, display, error
from Xlib.support import unix_connect
from Xlib.xobject.drawable import Window

from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils.log_utils import log

# 游戏进程名 (小写比较)
GAME_EXE_NAME: str = 'zenlesszonezero.exe'
# 游戏窗口可能的标题 (国际服 / 国服)
DEFAULT_WIN_TITLES: tuple[str, ...] = ('ZenlessZoneZero', '绝区零')

# 连接显示时会临时替换 python-xlib 的套接字函数 需要加锁
_connect_lock: threading.Lock = threading.Lock()

# X 连接相关的异常 出现时说明连接或窗口已失效
X_ERRORS: tuple[type[Exception], ...] = (error.XError, error.ConnectionClosedError, OSError)


def find_game_pids() -> list[int]:
    """在 /proc 里查找游戏进程。

    Returns:
        游戏进程号列表 新启动的在前
    """
    pids: list[int] = []
    for proc_dir in Path('/proc').iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            cmdline = (proc_dir / 'cmdline').read_bytes()
        except OSError:
            continue
        if not cmdline:
            continue
        argv0 = cmdline.split(b'\0', 1)[0].decode('utf-8', errors='ignore')
        exe_name = re.split(r'[\\/]', argv0)[-1].lower()
        if exe_name == GAME_EXE_NAME:
            pids.append(int(proc_dir.name))
    pids.sort(reverse=True)
    return pids


def get_process_display(pid: int) -> str | None:
    """读取进程环境变量中的 DISPLAY。

    Args:
        pid: 进程号

    Returns:
        DISPLAY 的值 读取失败时返回 None
    """
    try:
        environ = Path(f'/proc/{pid}/environ').read_bytes()
    except OSError:
        return None
    for item in environ.split(b'\0'):
        if item.startswith(b'DISPLAY='):
            value = item[len(b'DISPLAY='):].decode('utf-8', errors='ignore')
            return value or None
    return None


def open_display(display_name: str, pid: int | None = None) -> display.Display:
    """连接 X 显示。

    python-xlib 只会尝试宿主机的 /tmp/.X11-unix/X<n>。
    游戏在 flatpak 沙盒里时 套接字在沙盒自己的 /tmp 下, 宿主机能通过 /proc/<pid>/root 访问到,
    同时 Xwayland 也会监听抽象命名空间里的同名套接字。这里按以下顺序尝试:
    1. /proc/<pid>/root/tmp/.X11-unix/X<n> (游戏进程自己看到的套接字 最准确)
    2. 抽象命名空间 @/tmp/.X11-unix/X<n>
    3. python-xlib 默认方式

    Args:
        display_name: 显示名 例如 ':1'
        pid: 游戏进程号 用于定位沙盒里的套接字

    Returns:
        已连接的显示
    """
    m = re.match(r'^:(\d+)(\.\d+)?$', display_name)
    if m is None:  # 带主机名的显示 直接用默认方式
        return display.Display(display_name)

    dno = int(m.group(1))
    socket_paths: list[str] = []
    if pid is not None:
        socket_paths.append(f'/proc/{pid}/root/tmp/.X11-unix/X{dno}')
    socket_paths.append(f'\0/tmp/.X11-unix/X{dno}')

    original_get_socket = unix_connect.get_socket

    def get_socket(dname: str, host: str, display_no: int) -> socket.socket:
        for path in socket_paths:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
            except OSError:
                s.close()
                continue
            fcntl.fcntl(s.fileno(), fcntl.F_SETFD, fcntl.FD_CLOEXEC)
            return s
        return original_get_socket(dname, host, display_no)

    with _connect_lock:
        unix_connect.get_socket = get_socket
        try:
            return display.Display(display_name)
        finally:
            unix_connect.get_socket = original_get_socket


class GamescopeDisplay:
    """
    gamescope 里游戏的 X 显示与窗口
    游戏窗口 / 截图 / 键鼠输入共用同一个连接 (python-xlib 的连接是线程安全的)
    游戏关闭或重启后会自动重新查找
    """

    def __init__(self):
        self.win_title: str | None = None  # 额外接受的窗口标题

        self.game_pid: int | None = None
        self.display_name: str | None = None
        self.display: display.Display | None = None
        self.window: Window | None = None

        self._lock: threading.RLock = threading.RLock()

    def _is_connection_alive(self) -> bool:
        """当前连接是否可用 (游戏进程还在 且连接没有断开)"""
        if self.display is None or self.game_pid is None:
            return False
        if not os.path.exists(f'/proc/{self.game_pid}'):
            return False
        return self.display.display.socket_error is None

    def reset(self) -> None:
        """断开连接 并清空缓存 下次使用时重新查找"""
        with self._lock:
            if self.display is not None:
                with contextlib.suppress(Exception):
                    self.display.close()
            self.display = None
            self.window = None
            self.game_pid = None
            self.display_name = None

    def reset_window(self) -> None:
        """只清空缓存的窗口 下次使用时重新查找"""
        with self._lock:
            self.window = None

    def get_display(self) -> display.Display | None:
        """获取游戏所在的显示 必要时重新连接"""
        with self._lock:
            if self._is_connection_alive():
                return self.display
            self.reset()

            for pid in find_game_pids():
                display_name = get_process_display(pid)
                if display_name is None:
                    continue
                try:
                    d = open_display(display_name, pid)
                except Exception:
                    log.debug(f'连接游戏显示失败 pid={pid} DISPLAY={display_name}', exc_info=True)
                    continue
                d.set_error_handler(self._on_x_error)
                self.game_pid = pid
                self.display_name = display_name
                self.display = d
                log.info(f'已连接游戏显示 pid={pid} DISPLAY={display_name}')
                return d

            return None

    @staticmethod
    def _on_x_error(err: error.XError, request: object) -> None:
        """异步 X 错误 (没有回复的请求产生的) 只记录不抛出"""
        log.debug(f'X 错误: {err}')

    def get_window(self) -> Window | None:
        """获取游戏窗口 必要时重新查找"""
        with self._lock:
            d = self.get_display()
            if d is None:
                return None
            if self.window is not None:
                return self.window
            try:
                self.window = self._find_window(d)
            except X_ERRORS:
                log.debug('查找游戏窗口失败', exc_info=True)
                self.reset()
                return None
            if self.window is not None:
                log.info(f'已找到游戏窗口 {hex(self.window.id)}')
            return self.window

    def _find_window(self, d: display.Display) -> Window | None:
        """在根窗口下查找游戏窗口

        Args:
            d: 显示

        Returns:
            游戏窗口 找不到时返回 None
        """
        titles = set(DEFAULT_WIN_TITLES)
        if self.win_title:
            titles.add(self.win_title)

        root = d.screen().root
        candidates: list[Window] = []
        focused = root.get_full_property(d.intern_atom('GAMESCOPE_FOCUSED_WINDOW'), X.AnyPropertyType)
        if focused is not None and len(focused.value) > 0 and focused.value[0] != 0:
            candidates.append(d.create_resource_object('window', focused.value[0]))
        candidates.extend(root.query_tree().children)

        for win in candidates:
            try:
                if win.get_attributes().map_state != X.IsViewable:
                    continue
                if win.get_wm_name() in titles:
                    return win
            except error.XError:  # 窗口可能刚好被销毁
                continue
        return None

    def get_window_rect(self) -> Rect | None:
        """获取游戏窗口在其显示上的位置 (根窗口坐标)"""
        with self._lock:
            win = self.get_window()
            if win is None:
                return None
            try:
                geo = win.get_geometry()
            except X_ERRORS:
                # 窗口被销毁(例如游戏重建窗口) 或连接断开 下次重新查找
                log.debug('获取游戏窗口位置失败', exc_info=True)
                if self._is_connection_alive():
                    self.reset_window()
                else:
                    self.reset()
                return None
            if geo.width <= 1 or geo.height <= 1:
                return None
            return Rect(geo.x, geo.y, geo.x + geo.width, geo.y + geo.height)

    def on_x_failure(self) -> None:
        """使用连接时出错 判断连接是否失效 失效则重置"""
        with self._lock:
            if not self._is_connection_alive():
                self.reset()
            else:
                self.reset_window()
