import json
import os
import subprocess
import sys
from pathlib import Path

from one_dragon.base.operation.operation import Operation
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils import os_utils
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.enter_game.auto_hdr import DisableAutoHDR, EnableAutoHDR

if sys.platform != 'win32':
    from one_dragon.base.controller.gamescope_display import (
        find_game_pids,
        is_in_gamescope,
        kill_dead_game_sessions,
    )
    from one_dragon.utils.game_audio_linux import GAME_SINK_NAME, ensure_game_sink

# Linux 下通过 Sleepy Launcher (flatpak) 启动游戏 游戏必须运行在 gamescope 里 (一条龙的截图和输入都发到 gamescope 的显示上)
# 玩家自己玩时可能关掉了启动器里的 gamescope (gamescope 里鼠标转视角会卡顿)
# 所以一条龙不用玩家的启动器设置: 复制一份启动器配置 打开 gamescope 后放到单独的目录 用环境变量 LAUNCHER_FOLDER 让启动器读这份配置
# 配置里的游戏 / Wine 前缀 / 运行器等都是绝对路径 复制的配置仍然使用同一个游戏 玩家的设置不会被改动
SLEEPY_LAUNCHER_DATA_DIR: Path = Path.home() / '.var/app/moe.launcher.sleepy-launcher/data'
SLEEPY_LAUNCHER_FOLDER: Path = SLEEPY_LAUNCHER_DATA_DIR / 'sleepy-launcher'
ONE_DRAGON_LAUNCHER_FOLDER: Path = SLEEPY_LAUNCHER_DATA_DIR / 'sleepy-launcher-onedragon'
# --just-run-game: 与 --run-game 相同 但只有预下载可用时也直接启动游戏 (需要真正更新时仍会打开启动器窗口)
LINUX_LAUNCH_COMMAND: list[str] = [
    'flatpak', 'run', f'--env=LAUNCHER_FOLDER={ONE_DRAGON_LAUNCHER_FOLDER}',
    'moe.launcher.sleepy-launcher', '--just-run-game',
]


def write_gamescope_launcher_config() -> bool:
    """复制玩家的 Sleepy Launcher 配置 打开 gamescope 写到一条龙专用的启动器目录

    Returns:
        是否成功
    """
    config_path = SLEEPY_LAUNCHER_FOLDER / 'config.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        game = config['game']
        game['enhancements']['gamescope']['enabled'] = True
        # gamescope 的 WSI 层开着时 X 截图是黑屏
        game['environment']['ENABLE_GAMESCOPE_WSI'] = '0'
        # 游戏声音输出到静音用的虚拟输出 玩家听不到 声音闪避仍能录到 (见 game_audio_linux)
        # 虚拟输出创建失败时不设置 游戏正常发声
        if ensure_game_sink():
            game['environment']['PULSE_SINK'] = GAME_SINK_NAME
    except (OSError, ValueError, KeyError, TypeError):
        log.error(f'读取 Sleepy Launcher 配置失败 {config_path}', exc_info=True)
        return False
    ONE_DRAGON_LAUNCHER_FOLDER.mkdir(parents=True, exist_ok=True)
    (ONE_DRAGON_LAUNCHER_FOLDER / 'config.json').write_text(
        json.dumps(config, indent=4, ensure_ascii=False), encoding='utf-8'
    )
    return True


class OpenGame(Operation):

    def __init__(self, ctx: ZContext):
        self.ctx: ZContext = ctx
        Operation.__init__(self, ctx, op_name=gt('打开游戏'),
                           need_check_game_win=False)

    @operation_node(name='打开游戏', is_start_node=True, screenshot_before_round=False)
    def open_game(self) -> OperationRoundResult:
        """禁用自动 HDR + 启动游戏 exe。"""
        if sys.platform != 'win32':
            return self.open_game_linux()

        hdr_op = DisableAutoHDR(self.ctx)
        hdr_op.execute()

        if self.ctx.game_account_config.game_path == '':
            return self.round_fail('未配置游戏路径，请前往 [ 账户管理 ] -> [ 游戏路径 ] 手动设置')
        full_path = self.ctx.game_account_config.game_path
        dir_path = os.path.dirname(full_path)
        exe_name = os.path.basename(full_path)
        log.info('尝试自动启动游戏 路径为 %s', full_path)
        command = f'cmd /c "start "" /d "{dir_path}" "{exe_name}"'
        if self.ctx.game_config.launch_argument:
            screen_size = self.ctx.game_config.screen_size
            screen_width = screen_size.split('x')[0]
            screen_height = screen_size.split('x')[1]
            full_screen = self.ctx.game_config.full_screen
            popup_window = "-popupwindow" if self.ctx.game_config.popup_window else ""
            monitor = self.ctx.game_config.monitor
            arguement = (f'{self.ctx.game_config.launch_argument_advance}'
                         f' -screen-width {screen_width} -screen-height {screen_height}'
                         f' -screen-fullscreen {full_screen} {popup_window} -monitor {monitor}')
            command = f'{command} {arguement}'
        command = f'{command} & exit"'
        log.info('命令行指令 %s', command)

        if not getattr(sys, 'frozen', False):
            # CREATE_BREAKAWAY_FROM_JOB:启动器用进程组管理时,使子进程逃离 jobobject,
            # 避免 OneDragon-Launcher.exe 退出后游戏被杀死。
            subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB
            )
        else:
            subprocess.Popen(command)

        return self.round_success(wait=5)

    def open_game_linux(self) -> OperationRoundResult:
        """
        Linux 下用 Sleepy Launcher 启动游戏。
        不需要配置游戏路径 也没有自动 HDR 的注册表设置。
        """
        pids = find_game_pids()
        if any(is_in_gamescope(pid) for pid in pids):
            log.info('游戏进程已存在 不重复启动 直接等待游戏窗口')
            return self.round_success()
        if len(pids) > 0:
            return self.round_fail('游戏没有在 gamescope 里运行 一条龙无法控制 请先关闭游戏 再由一条龙启动')

        # 之前游戏崩溃或退出后 残留的 gamescope 会话会一直占着窗口和显存 先清理掉
        # 只清理启动超过 1 分钟的 避免误伤刚由启动器打开、游戏进程还没出现的会话
        kill_dead_game_sessions(min_age_seconds=60)

        if not write_gamescope_launcher_config():
            return self.round_fail('读取 Sleepy Launcher 配置失败 请先在启动器里装好游戏')

        log.info('尝试自动启动游戏 命令行指令 %s', ' '.join(LINUX_LAUNCH_COMMAND))
        # flatpak 会把环境变量传进沙盒 以下变量会让启动失败 需要去掉:
        # - SSL_CERT_FILE / SSL_CERT_DIR: 宿主机的证书路径在沙盒里不存在 Sleepy Launcher 读不到证书会直接崩溃
        # - NoDefaultCurrentDirectoryInExePath: Windows 变量 Wine 也认 设置后按相对路径找不到游戏 exe
        env = {
            k: v for k, v in os.environ.items()
            if k not in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'NoDefaultCurrentDirectoryInExePath')
        }
        # 启动器的输出写到日志文件 启动失败时可以排查
        launcher_log_path = Path(os_utils.get_path_under_work_dir('.log')) / 'sleepy_launcher.log'
        with launcher_log_path.open('w', encoding='utf-8') as launcher_log:
            # 新建会话 一条龙退出后游戏不会被一起结束
            subprocess.Popen(
                LINUX_LAUNCH_COMMAND,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=launcher_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return self.round_success(wait=5)

    @node_from(from_name='打开游戏')
    @operation_node(name='等待游戏打开', node_max_retry_times=60, screenshot_before_round=False)
    def wait_game(self) -> OperationRoundResult:
        """等游戏窗口就绪 → 激活窗口 + 恢复 HDR。"""
        self.ctx.controller.init_game_win()
        if self.ctx.controller.is_game_window_ready:
            self.ctx.controller.active_window()
            if sys.platform == 'win32':
                hdr_op = EnableAutoHDR(self.ctx)
                hdr_op.execute()
            return self.round_success()
        else:
            # Linux 下 Wine 和 gamescope 启动较慢 每次多等一会 总共约 3 分钟
            return self.round_retry(wait=1 if sys.platform == 'win32' else 3)
