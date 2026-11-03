from one_dragon.base.controller.pc_game_window_linux import PcGameWindowLinux
from one_dragon.base.controller.pc_screenshot.pc_screenshot_controller import (
    PcScreenshotController,
)
from one_dragon.base.controller.pc_screenshot.screencapper_base import ScreencapperBase
from one_dragon.base.controller.pc_screenshot.xshm_screencapper import XShmScreencapper

# Linux 下唯一的截图方法
XSHM_METHOD: str = 'xshm'


class PcScreenshotControllerLinux(PcScreenshotController):
    """
    Linux 下的截图控制器
    只有 X 共享内存一种截图方法 设置里选择的任何截图方法都使用它
    """

    def __init__(self, game_win: PcGameWindowLinux, standard_width: int, standard_height: int):
        PcScreenshotController.__init__(self, game_win, standard_width, standard_height)
        self.strategies: dict[str, ScreencapperBase] = {
            XSHM_METHOD: XShmScreencapper(game_win, standard_width, standard_height),
        }

    def init_screenshot(self, method: str) -> str | None:
        """初始化截图方法 已经初始化过时不重复初始化

        Args:
            method: 设置里的截图方法 Linux 下忽略

        Returns:
            成功初始化的方法名称
        """
        if self.active_strategy_name is not None:
            return self.active_strategy_name
        return PcScreenshotController.init_screenshot(self, XSHM_METHOD)

    def _get_method_priority_list(self, method: str) -> list:
        return [XSHM_METHOD]
