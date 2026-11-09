"""Linux 下从 gamescope 的 PipeWire 画面流截图。

游戏开着 gamescope 的 WSI 层时 (默认), 画面直接交给 gamescope, X 截图是黑屏。
gamescope 会把合成后的画面发布成一个 PipeWire 视频源 (Steam 录像用的就是它), 这里用 GStreamer 读取。

前提: 游戏的 flatpak 沙盒要能访问 PipeWire 套接字 (`--filesystem=xdg-run/pipewire-0`), 否则 gamescope 不会发布画面流。

后台一直接收画面流, 只保留最新一帧 (appsink drop=true), 截图时取这一帧 最多比游戏画面晚一帧。
"""

from __future__ import annotations

import json
import subprocess
import threading

import cv2
import gi
import numpy as np
from cv2.typing import MatLike

from one_dragon.base.controller.gamescope_display import is_gamescope_wsi_enabled
from one_dragon.base.controller.pc_game_window_linux import PcGameWindowLinux
from one_dragon.base.controller.pc_screenshot.screencapper_base import ScreencapperBase
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils.log_utils import log

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp  # noqa: E402

# 第一次连接画面流后 等第一帧的最长时间 (秒)
FIRST_FRAME_TIMEOUT_SECONDS: float = 1.0


def find_gamescope_stream_serial() -> int | None:
    """用 pw-dump 查找 gamescope 发布的画面流。

    Returns:
        画面流节点的 object.serial 有多个时取最新的 没有时返回 None
    """
    try:
        output = subprocess.run(['pw-dump'], capture_output=True, timeout=5, check=True).stdout
        objects = json.loads(output)
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug('pw-dump 失败', exc_info=True)
        return None

    serials: list[int] = []
    for obj in objects:
        props = (obj.get('info') or {}).get('props') or {}
        if props.get('media.class') == 'Video/Source' and str(props.get('node.name', '')).startswith('gamescope'):
            serials.append(int(props['object.serial']))
    return max(serials) if serials else None


class PipeWireScreencapper(ScreencapperBase):
    """从 gamescope 的 PipeWire 画面流截图的策略 返回 RGB 图像"""

    def __init__(self, game_win: PcGameWindowLinux, standard_width: int, standard_height: int):
        ScreencapperBase.__init__(self, game_win, standard_width, standard_height)
        self.game_win: PcGameWindowLinux = game_win
        self._lock: threading.RLock = threading.RLock()

        self._pipeline: Gst.Pipeline | None = None
        self._appsink: GstApp.AppSink | None = None
        self._game_pid: int | None = None  # 画面流对应的游戏进程 游戏重启后重新连接
        self._last_frame: np.ndarray | None = None  # 最新一帧 BGRX (h, w, 4)
        self._warned_no_stream: bool = False

    def init(self) -> bool:
        """初始化截图器 游戏未就绪时也返回成功 截图时再连接画面流

        Returns:
            是否初始化成功
        """
        Gst.init(None)
        return True

    def _start(self, serial: int) -> bool:
        """连接画面流

        Args:
            serial: 画面流节点的 object.serial

        Returns:
            是否成功
        """
        # WirePlumber 的自动连接脚本要求客户端带 media.type 等属性 不带时会报 target not found
        # always-copy=true: 收到一帧就复制出来 立即把缓冲区还给 gamescope
        # 不复制时 pipewiresrc 会一直占着 gamescope 的缓冲区 gamescope 每秒报几十次 out of buffers
        pipeline = Gst.parse_launch(
            f'pipewiresrc target-object={serial} do-timestamp=false always-copy=true'
            ' stream-properties="props,media.type=Video,media.category=Capture,media.role=Screen"'
            ' ! video/x-raw,format=BGRx'
            ' ! appsink name=sink max-buffers=1 drop=true sync=false emit-signals=false'
        )
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            return False
        self._pipeline = pipeline
        self._appsink = pipeline.get_by_name('sink')
        log.info(f'已连接 gamescope 画面流 serial={serial}')
        return True

    def _check_bus(self) -> bool:
        """检查画面流是否出错或结束 (例如 gamescope 退出)

        Returns:
            画面流是否还可用
        """
        bus = self._pipeline.get_bus()
        msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg is None:
            return True
        if msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            log.debug(f'画面流出错 {err.message}')
        return False

    def _pull_frame(self, timeout_seconds: float) -> None:
        """取出最新一帧 没有新帧时保留上一帧

        Args:
            timeout_seconds: 等待新帧的最长时间
        """
        sample = self._appsink.try_pull_sample(int(timeout_seconds * Gst.SECOND))
        if sample is None:
            return
        structure = sample.get_caps().get_structure(0)
        width = structure.get_value('width')
        height = structure.get_value('height')
        buffer = sample.get_buffer()
        ok, map_info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            # 每行可能有对齐填充 按实际行宽取
            stride = len(map_info.data) // height
            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape(height, stride)[:, :width * 4]
            self._last_frame = frame.reshape(height, width, 4).copy()
        finally:
            buffer.unmap(map_info)

    def capture(self, rect: Rect, independent: bool = False) -> MatLike | None:
        """截图

        Args:
            rect: 游戏窗口区域
            independent: 是否独立截图

        Returns:
            RGB 截图 失败返回 None (游戏关着 WSI 层时也返回 None 交给 X 截图)
        """
        with self._lock:
            game_pid = self.game_win.gamescope.game_pid
            if game_pid is None or not is_gamescope_wsi_enabled(game_pid):
                return None

            if self._pipeline is not None and (game_pid != self._game_pid or not self._check_bus()):
                self.cleanup()

            first_frame = False
            if self._pipeline is None:
                serial = find_gamescope_stream_serial()
                if serial is None:
                    if not self._warned_no_stream:
                        log.error('找不到 gamescope 的 PipeWire 画面流 无法截图。'
                                  '请先运行 flatpak override --user --filesystem=xdg-run/pipewire-0 moe.launcher.sleepy-launcher 再重启游戏')
                        self._warned_no_stream = True
                    return None
                if not self._start(serial):
                    return None
                self._game_pid = game_pid
                self._warned_no_stream = False
                first_frame = True

            self._pull_frame(FIRST_FRAME_TIMEOUT_SECONDS if first_frame else 0)
            if self._last_frame is None:
                return None

            result = cv2.cvtColor(self._last_frame, cv2.COLOR_BGRA2RGB)
            if result.shape[1] != rect.width or result.shape[0] != rect.height:
                # gamescope 输出分辨率与游戏窗口不同时 缩放到窗口大小 保持与 X 截图一致
                result = cv2.resize(result, (rect.width, rect.height))
            return result

    def cleanup(self) -> None:
        """断开画面流"""
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._appsink = None
            self._game_pid = None
            self._last_frame = None
