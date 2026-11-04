"""Linux 下的 PC 控制器 游戏运行在 gamescope 里。

- 窗口: 在 gamescope 的 X 显示上查找游戏窗口
- 截图: X 共享内存 (MIT-SHM)
- 输入: XTest 只发到游戏所在的显示 不影响宿主桌面

因为输入本身就不影响桌面 前台模式和后台模式都使用同一套 XTest 输入 不使用虚拟手柄。
"""

from __future__ import annotations

import os
import signal
import time
from typing import TYPE_CHECKING

from one_dragon.base.controller.gamescope_display import (
    find_game_pids,
    kill_dead_game_sessions,
)
from one_dragon.base.controller.pc_button.xtest_keyboard_mouse_controller import (
    TAP_HOLD_SECONDS,
    XTestKeyboardMouseController,
)
from one_dragon.base.controller.pc_controller_base import PcControllerBase
from one_dragon.base.controller.pc_game_window_linux import PcGameWindowLinux
from one_dragon.base.controller.pc_screenshot.pc_screenshot_controller_linux import (
    PcScreenshotControllerLinux,
)
from one_dragon.base.geometry.point import Point
from one_dragon.utils.log_utils import log

if TYPE_CHECKING:
    from one_dragon.base.controller.drag_timing import DragTiming

# 相对移动的放大系数 让同样的像素值转动的角度与 Windows 一致
# 同一游戏内镜头灵敏度(X轴)=3(默认值)下实测: Windows 经验值 5.5 像素/度 (turn_dx 默认 -5.5),
# gamescope + Wine 下 XTest 相对移动约 9.0 像素/度 (与事件拆分方式无关 没有指针加速)。
# 业务里有很多固定像素的转向 (自动战斗 AtomicTurn、式舆防卫战、恶名狩猎等) 所以在这里统一换算。
RELATIVE_MOVE_SCALE: float = 9.0 / 5.5


class PcControllerLinux(PcControllerBase):

    def __init__(self,
                 screenshot_method: str,
                 standard_width: int = 1920,
                 standard_height: int = 1080):
        PcControllerBase.__init__(self,
                                  screenshot_method=screenshot_method,
                                  standard_width=standard_width,
                                  standard_height=standard_height)
        self.game_win: PcGameWindowLinux = PcGameWindowLinux(standard_width, standard_height)
        self.keyboard_controller: XTestKeyboardMouseController = XTestKeyboardMouseController(self.game_win.gamescope)
        self.btn_controller: XTestKeyboardMouseController = self.keyboard_controller
        self.screenshot_controller: PcScreenshotControllerLinux = PcScreenshotControllerLinux(
            self.game_win, standard_width, standard_height
        )

    def close_game(self) -> None:
        """结束游戏进程 再清理残留的 Wine 进程 让 gamescope 和启动器退出"""
        pids = find_game_pids()
        if len(pids) == 0:
            kill_dead_game_sessions()
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                log.error(f'关闭游戏失败 pid={pid}', exc_info=True)
                return
        self.game_win.gamescope.reset()
        # 等游戏进程退出 (最多 10 秒) 之后残留的 winedevice.exe 等进程会让 gamescope 一直不关
        for _ in range(20):
            if len(find_game_pids()) == 0:
                break
            time.sleep(0.5)
        kill_dead_game_sessions()
        log.info('关闭游戏成功')

    def enable_foreground_mode(self) -> None:
        """启用前台模式 Linux 下与后台模式相同 都使用 XTest 键鼠"""
        self.background_mode = False
        self._game_input_mode = 'keyboard_mouse'
        self.enable_keyboard()
        log.info('已启用前台模式: XTest 键鼠 (gamescope)')

    def enable_background_mode(self, gamepad_type: str = 'xbox') -> None:
        """启用后台模式 Linux 下输入只发到 gamescope 不影响桌面 本身就是后台的 不需要虚拟手柄

        Args:
            gamepad_type: Linux 下忽略
        """
        self.background_mode = False
        self._game_input_mode = 'keyboard_mouse'
        self.enable_keyboard()
        log.info('Linux 下 XTest 键鼠本身就在后台运行 后台模式无需虚拟手柄')

    def _ensure_mouse_mode(self) -> bool:
        """Linux 下游戏一直是键鼠模式"""
        return True

    def _send_activate(self) -> bool:
        """gamescope 里游戏一直有焦点 不需要激活"""
        return True

    def click(self, pos: Point = None, press_time: float = 0.1, pc_alt: bool = False,
              gamepad_key: str | None = None) -> bool:
        """点击位置。

        Args:
            pos: 游戏中的位置 (x,y) None 时点击当前鼠标位置
            press_time: 大于0时长按若干秒
            pc_alt: 按住 ALT 键进行点击 (大世界里光标被锁定 需要 ALT 解锁)
            gamepad_key: Linux 下不使用手柄 忽略

        Returns:
            不在窗口区域时不点击 返回False
        """
        click_pos: Point | None = None
        if pos is not None:
            click_pos = self.game_win.game2win_pos(pos)
            if click_pos is None:
                log.error('点击非游戏窗口区域 (%s)', pos)
                return False

        xtest = self.keyboard_controller
        if pc_alt:
            xtest.key_down('alt')
            time.sleep(0.2)
        try:
            if click_pos is not None:
                xtest.move_to(click_pos)
                time.sleep(0.02)
            xtest.mouse_down()
            time.sleep(max(TAP_HOLD_SECONDS, press_time))
            xtest.mouse_up()
        finally:
            if pc_alt:
                xtest.key_up('alt')
        return True

    def drag_to(
        self, end: Point, start: Point | None = None, duration: float = 0.5,
        *, timing: DragTiming | None = None,
    ) -> None:
        """按住拖拽。

        Args:
            end: 拖拽目的点。
            start: 拖拽开始点。None 时从当前鼠标位置开始
            duration: 未指定 timing 时的原有时长预算。
            timing: 显式指定三段时序；提供时忽略 duration，None 保留原行为。
        """
        xtest = self.keyboard_controller
        if start is None:
            from_pos = xtest.get_position()
        else:
            from_pos = self.game_win.game2win_pos(start)
        if from_pos is None:
            log.error('拖拽起点不在游戏窗口区域 (%s)', start)
            return
        to_pos = self.game_win.game2win_pos(end)
        if to_pos is None:
            log.error('拖拽终点不在游戏窗口区域 (%s)', end)
            return

        xtest.move_to(from_pos)
        time.sleep(0.02)
        xtest.mouse_down()
        try:
            press_wait = 0.02 if timing is None else timing.press_wait
            if press_wait > 0:
                time.sleep(press_wait)

            release_wait = PcControllerBase.SLEEP_BEFORE_DRAG_END if timing is None else timing.release_wait
            move_duration = duration - release_wait if timing is None else timing.move_duration
            duration_drag = max(move_duration, PcControllerBase.DRAG_MIN_DURATION)
            # 分步移动到终点
            steps = max(int(duration_drag / 0.02), 5)
            for i in range(1, steps + 1):
                t = i / steps
                xtest.move_to(Point(from_pos.x + (to_pos.x - from_pos.x) * t,
                                    from_pos.y + (to_pos.y - from_pos.y) * t))
                time.sleep(duration_drag / steps)
            # 松开之前先定住鼠标以消除滑动惯性
            time.sleep(release_wait)
        finally:
            xtest.mouse_up()

    def scroll(self, down: int, pos: Point | None = None) -> None:
        """向下滚动。

        Args:
            down: 负数时为向上滚动
            pos: 滚动位置 默认分辨率下的游戏窗口里的坐标 None 时在当前鼠标位置滚动
        """
        if pos is not None:
            win_pos = self.game_win.game2win_pos(pos)
            if win_pos is None:
                log.error('滚动位置不在游戏窗口区域 (%s)', pos)
                return
            self.keyboard_controller.move_to(win_pos)
            time.sleep(0.02)
        self.keyboard_controller.scroll(down)

    def input_str(self, to_input: str, interval: float = 0.1) -> None:
        """输入文本 需要自己先选择好输入框。

        Args:
            to_input: 文本
            interval: 与 Windows 一致 不使用
        """
        self.keyboard_controller.type_text(to_input)

    def mouse_move(self, game_pos: Point) -> None:
        """
        鼠标移动到指定的位置
        """
        win_pos = self.game_win.game2win_pos(game_pos)
        if win_pos is not None:
            self.keyboard_controller.move_to(win_pos)
            time.sleep(0.1)

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        """鼠标相对移动 用于转动视角

        Args:
            dx: 横向移动距离，正数向右
            dy: 纵向移动距离，正数向下 (与 Windows 下的像素值同一口径 内部乘以 RELATIVE_MOVE_SCALE)
        """
        self.keyboard_controller.move_relative(round(dx * RELATIVE_MOVE_SCALE), round(dy * RELATIVE_MOVE_SCALE))
