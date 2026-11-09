from one_dragon.base.controller.pc_game_window_linux import PcGameWindowLinux
from one_dragon.base.controller.pc_screenshot.pc_screenshot_controller import (
    PcScreenshotController,
)
from one_dragon.base.controller.pc_screenshot.pipewire_screencapper import (
    PipeWireScreencapper,
)
from one_dragon.base.controller.pc_screenshot.screencapper_base import ScreencapperBase
from one_dragon.base.controller.pc_screenshot.xshm_screencapper import XShmScreencapper

# Linux 下的截图方法 按游戏是否开着 gamescope 的 WSI 层自动选择 设置里的选择不生效
PIPEWIRE_METHOD: str = 'pipewire'  # 开着 WSI 层 (默认): 从 gamescope 的 PipeWire 画面流截图
XSHM_METHOD: str = 'xshm'  # 关着 WSI 层 (ENABLE_GAMESCOPE_WSI=0): X 共享内存截图


class PcScreenshotControllerLinux(PcScreenshotController):
    """
    Linux 下的截图控制器
    两种截图方法各自判断游戏是否开着 WSI 层 不适用时返回 None 由基类换下一种方法
    """

    def __init__(self, game_win: PcGameWindowLinux, standard_width: int, standard_height: int):
        PcScreenshotController.__init__(self, game_win, standard_width, standard_height)
        self.strategies: dict[str, ScreencapperBase] = {
            PIPEWIRE_METHOD: PipeWireScreencapper(game_win, standard_width, standard_height),
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
        return PcScreenshotController.init_screenshot(self, PIPEWIRE_METHOD)

    def _get_method_priority_list(self, method: str) -> list:
        return [PIPEWIRE_METHOD, XSHM_METHOD]
