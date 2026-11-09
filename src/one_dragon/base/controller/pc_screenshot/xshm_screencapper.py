"""Linux 下用 X 共享内存 (MIT-SHM) 截取 gamescope 里的游戏窗口。

python-xlib 没有 MIT-SHM 扩展 这里补上需要的三个请求 共享内存段用 libc 的 shmget/shmat 创建。
服务端把窗口画面直接写进共享内存 客户端只收到一个很小的回复 1080p 一帧约 2ms。
"""

from __future__ import annotations

import ctypes
import threading

import cv2
import numpy as np
from cv2.typing import MatLike
from Xlib import X, display
from Xlib.protocol import rq

from one_dragon.base.controller.gamescope_display import (
    X_ERRORS,
    is_gamescope_wsi_enabled,
)
from one_dragon.base.controller.pc_game_window_linux import PcGameWindowLinux
from one_dragon.base.controller.pc_screenshot.screencapper_base import ScreencapperBase
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils.log_utils import log

SHM_EXT_NAME: str = 'MIT-SHM'

_IPC_PRIVATE: int = 0
_IPC_CREAT: int = 0o1000
_IPC_RMID: int = 0

_libc = ctypes.CDLL(None, use_errno=True)
_libc.shmget.restype = ctypes.c_int
_libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
_libc.shmat.restype = ctypes.c_void_p
_libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
_libc.shmdt.restype = ctypes.c_int
_libc.shmdt.argtypes = [ctypes.c_void_p]
_libc.shmctl.restype = ctypes.c_int
_libc.shmctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]


class _ShmAttach(rq.Request):
    _request = rq.Struct(
        rq.Card8('opcode'),
        rq.Opcode(1),
        rq.RequestLength(),
        rq.Card32('shmseg'),
        rq.Card32('shmid'),
        rq.Bool('read_only'),
        rq.Pad(3),
    )


class _ShmDetach(rq.Request):
    _request = rq.Struct(
        rq.Card8('opcode'),
        rq.Opcode(2),
        rq.RequestLength(),
        rq.Card32('shmseg'),
    )


class _ShmGetImage(rq.ReplyRequest):
    _request = rq.Struct(
        rq.Card8('opcode'),
        rq.Opcode(4),
        rq.RequestLength(),
        rq.Drawable('drawable'),
        rq.Int16('x'),
        rq.Int16('y'),
        rq.Card16('width'),
        rq.Card16('height'),
        rq.Card32('plane_mask'),
        rq.Card8('format'),
        rq.Pad(3),
        rq.Card32('shmseg'),
        rq.Card32('offset'),
    )

    _reply = rq.Struct(
        rq.ReplyCode(),
        rq.Card8('depth'),
        rq.Card16('sequence_number'),
        rq.ReplyLength(),
        rq.Card32('visual'),
        rq.Card32('size'),
        rq.Pad(16),
    )


class XShmScreencapper(ScreencapperBase):
    """使用 X 共享内存截取 gamescope 里游戏窗口的策略 返回 RGB 图像"""

    def __init__(self, game_win: PcGameWindowLinux, standard_width: int, standard_height: int):
        ScreencapperBase.__init__(self, game_win, standard_width, standard_height)
        self.game_win: PcGameWindowLinux = game_win
        self._lock: threading.RLock = threading.RLock()

        self._display: display.Display | None = None  # 共享内存段挂载到的连接
        self._opcode: int = 0
        self._shmseg: int = 0
        self._shm_addr: int | None = None
        self._buffer: np.ndarray | None = None  # 指向共享内存的数组 (h, w, 4)

    def init(self) -> bool:
        """初始化截图器 游戏窗口未就绪时也返回成功 截图时再挂载共享内存

        Returns:
            是否初始化成功
        """
        return True

    def _attach(self, d: display.Display, width: int, height: int) -> bool:
        """创建共享内存段 并挂载到 X 连接上

        Args:
            d: X 显示连接
            width: 宽度
            height: 高度

        Returns:
            是否成功
        """
        self.cleanup()
        ext = d.query_extension(SHM_EXT_NAME)
        if ext is None:
            log.error('游戏显示不支持 MIT-SHM 无法截图')
            return False

        size = width * height * 4
        shmid = _libc.shmget(_IPC_PRIVATE, size, _IPC_CREAT | 0o600)
        if shmid < 0:
            log.error(f'创建共享内存失败 errno={ctypes.get_errno()}')
            return False
        addr = _libc.shmat(shmid, None, 0)
        if addr is None or addr == ctypes.c_void_p(-1).value:
            log.error(f'挂载共享内存失败 errno={ctypes.get_errno()}')
            _libc.shmctl(shmid, _IPC_RMID, None)
            return False

        try:
            shmseg = d.display.allocate_resource_id()
            _ShmAttach(display=d.display, opcode=ext.major_opcode, shmseg=shmseg, shmid=shmid, read_only=False)
            d.sync()
        finally:
            # 标记删除 双方都断开后系统自动回收
            _libc.shmctl(shmid, _IPC_RMID, None)

        self._display = d
        self._opcode = ext.major_opcode
        self._shmseg = shmseg
        self._shm_addr = addr
        self._buffer = np.ctypeslib.as_array((ctypes.c_uint8 * size).from_address(addr)).reshape(height, width, 4)
        return True

    def capture(self, rect: Rect, independent: bool = False) -> MatLike | None:
        """截图

        Args:
            rect: 游戏窗口区域
            independent: 是否独立截图

        Returns:
            RGB 截图 失败返回 None (游戏开着 gamescope 的 WSI 层时 X 截图是黑屏 也返回 None)
        """
        with self._lock:
            gamescope = self.game_win.gamescope
            win = gamescope.get_window()
            d = gamescope.display
            if win is None or d is None:
                return None
            if is_gamescope_wsi_enabled(gamescope.game_pid):
                return None

            width, height = rect.width, rect.height
            if (self._display is not d or self._buffer is None
                    or self._buffer.shape[0] != height or self._buffer.shape[1] != width):
                try:
                    if not self._attach(d, width, height):
                        return None
                except X_ERRORS:
                    log.debug('挂载共享内存失败', exc_info=True)
                    gamescope.on_x_failure()
                    self.cleanup()
                    return None

            try:
                _ShmGetImage(
                    display=d.display, opcode=self._opcode, drawable=win,
                    x=0, y=0, width=width, height=height,
                    plane_mask=0xFFFFFFFF, format=X.ZPixmap,
                    shmseg=self._shmseg, offset=0,
                )
            except X_ERRORS:
                log.debug('共享内存截图失败', exc_info=True)
                gamescope.on_x_failure()
                return None

            # 内存里是 BGRX 转换时会复制一份 共享内存可以继续复用
            return cv2.cvtColor(self._buffer, cv2.COLOR_BGRA2RGB)

    def cleanup(self) -> None:
        """断开并释放共享内存"""
        with self._lock:
            if self._display is not None and self._shmseg:
                try:
                    _ShmDetach(display=self._display.display, opcode=self._opcode, shmseg=self._shmseg)
                    self._display.flush()
                except Exception:
                    pass
            self._buffer = None
            if self._shm_addr is not None:
                _libc.shmdt(self._shm_addr)
            self._display = None
            self._opcode = 0
            self._shmseg = 0
            self._shm_addr = None
