"""Linux 下一条龙启动的游戏静音

一条龙启动游戏时 让游戏的声音输出到一个不接任何设备的虚拟输出 (PulseAudio 的 null sink) 玩家听不到;
声音闪避从这个虚拟输出的监听口 (monitor) 录音 仍然能听到游戏声音。
不直接把游戏静音 (或音量调 0) 是因为那样游戏声音就不会进入任何监听口 声音闪避会失效。

游戏是通过环境变量 PULSE_SINK 指定输出的 只加在一条龙自己的启动器配置里 玩家自己启动游戏时不受影响。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from one_dragon.base.controller.gamescope_display import find_game_pids, is_in_gamescope
from one_dragon.utils.log_utils import log

# 虚拟输出的名称 也是游戏环境变量 PULSE_SINK 的值
GAME_SINK_NAME: str = 'onedragon_game'


def ensure_game_sink() -> bool:
    """确保虚拟输出存在 不存在时创建

    Returns:
        是否存在 (或创建成功)
    """
    try:
        sinks = subprocess.run(['pactl', 'list', 'short', 'sinks'],
                               capture_output=True, text=True, timeout=5, check=True).stdout
        if any(line.split('\t')[1] == GAME_SINK_NAME for line in sinks.splitlines() if '\t' in line):
            return True
        subprocess.run(['pactl', 'load-module', 'module-null-sink', f'sink_name={GAME_SINK_NAME}',
                        'sink_properties=device.description=OneDragon-Game'],
                       capture_output=True, text=True, timeout=5, check=True)
        log.info(f'已创建游戏静音用的虚拟输出 {GAME_SINK_NAME}')
        return True
    except (OSError, subprocess.SubprocessError, IndexError):
        log.error('创建游戏静音用的虚拟输出失败 游戏会正常发声', exc_info=True)
        return False


def get_game_loopback_id() -> str | None:
    """游戏的声音输出到虚拟输出时 返回虚拟输出的监听口 id (声音闪避从这里录音)

    Returns:
        监听口 id 游戏没有输出到虚拟输出时返回 None
    """
    for pid in find_game_pids():
        if not is_in_gamescope(pid):
            continue
        try:
            environ = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if f'PULSE_SINK={GAME_SINK_NAME}'.encode() in environ:
            return f'{GAME_SINK_NAME}.monitor'
    return None
