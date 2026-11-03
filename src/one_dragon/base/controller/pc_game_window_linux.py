from __future__ import annotations

from Xlib.xobject.drawable import Window

from one_dragon.base.controller.gamescope_display import GamescopeDisplay
from one_dragon.base.controller.pc_game_window import PcGameWindow
from one_dragon.base.geometry.rectangle import Rect


class PcGameWindowLinux(PcGameWindow):
    """
    Linux 下的游戏窗口 游戏运行在 gamescope 里
    对外接口与 PcGameWindow 一致 坐标是游戏所在 X 显示的根窗口坐标
    gamescope 会一直让游戏保持焦点 所以窗口总是"激活"的 激活操作不需要做任何事
    """

    def __init__(self,
                 standard_width: int = 1920,
                 standard_height: int = 1080):
        PcGameWindow.__init__(self, standard_width, standard_height)
        self.gamescope: GamescopeDisplay = GamescopeDisplay()

    def _clear_cached_window(self) -> None:
        self.gamescope.win_title = self.win_title
        self.gamescope.reset_window()

    def _reset_cached_window(self) -> None:
        self._clear_cached_window()

    def init_win(self) -> None:
        """
        初始化窗口
        """
        self.gamescope.win_title = self.win_title
        self.gamescope.get_window()

    def get_win(self) -> Window | None:
        return self.gamescope.get_window()

    def get_hwnd(self) -> int | None:
        """返回 X 窗口 id"""
        win = self.gamescope.get_window()
        return win.id if win is not None else None

    @property
    def is_win_valid(self) -> bool:
        """
        当前窗口是否正常
        """
        return self.gamescope.get_window_rect() is not None

    @property
    def is_win_active(self) -> bool:
        """
        是否当前激活的窗口 gamescope 里游戏总是有焦点
        """
        return self.is_win_valid

    def active(self) -> bool:
        """
        激活窗口 gamescope 里游戏总是有焦点 不需要操作
        """
        return self.is_win_valid

    @property
    def win_rect(self) -> Rect | None:
        """
        获取游戏窗口在其 X 显示上的位置
        """
        return self.gamescope.get_window_rect()
