# Linux 运行说明

> 在 Linux 上从源码运行一条龙：需要的环境、怎么启动、Linux 后端怎么实现、已知限制。
> Windows 的环境搭建见 [快速开始](quickstart.md)，本文只写 Linux 不同的地方。

**适用范围**：这是个人移植，只在一台机器上验证过（Ubuntu 26.04、KDE Plasma 6.6 Wayland、单个 1920x1080 显示器、NVIDIA RTX 4060）。
游戏必须运行在 **gamescope** 里，所有截图和键鼠输入都发到 gamescope 自己的 X 显示，不经过宿主桌面。
不支持不带 gamescope 直接运行游戏，也不支持虚拟手柄。

## 1. 环境要求

### 1.1. 游戏

- 用 [Sleepy Launcher](https://github.com/an-anime-team/sleepy-launcher)（flatpak `moe.launcher.sleepy-launcher`）安装和启动游戏。
- 安装 gamescope 的 flatpak 扩展：

  ```shell
  flatpak install org.freedesktop.Platform.VulkanLayer.gamescope//25.08
  ```

- Sleepy Launcher 设置里：gamescope 的分辨率设置（gamescope 窗口和游戏都用 1920x1080，项目的坐标都基于 1080p）。gamescope 开关本身不用打开，一条龙启动游戏时会自己打开（见 [2. 安装和启动](#2-安装和启动)）。
  - 自己玩时建议关掉 gamescope：gamescope 里用鼠标转视角会严重卡顿（FSR、gamemode、强制捕获光标、自适应同步都试过，无关，只跟 gamescope 开关有关）。
  - 一条龙只控制运行在 gamescope 里的游戏。自己开着游戏（没有 gamescope）时运行一条龙，会提示先关闭游戏。
- 游戏内设置：
  - 文本语言用 **简体中文** 或 **English**。项目的文字识别按这两种语言准备，繁体中文下很多画面认不出来。
  - 想用声音闪避时，游戏的 **主音量必须大于 0**。声音是从系统的声音输出的监听口（monitor）录的，游戏静音时录到的就是一片静音。系统音量或系统静音不影响。
  - 镜头灵敏度：转向的像素数按游戏默认灵敏度 3 设计。用别的灵敏度时，固定像素的转向（自动战斗模板里的转向、式舆防卫战等）会转多或转少，与 Windows 上一样。改灵敏度后在一条龙里跑一次「灵敏度校准」，更新 `turn_dx`。详见 [转向与灵敏度](../zzz/turn_sensitivity.md)。

### 1.2. 系统

- 当前用户要在 `input` 组里（全局快捷键要读 `/dev/input`）：

  ```shell
  sudo usermod -aG input $USER   # 需要重新登录才生效
  ```

- NVIDIA 显卡：只需要显卡驱动。CUDA / cuDNN 运行库由 pip 包 `onnxruntime-gpu[cuda,cudnn]` 带上，不需要装系统 CUDA。
- `uv`：见 [快速开始](quickstart.md) 第 2 步（Linux 用 `curl -LsSf https://astral.sh/uv/install.sh | sh`）。
- `evdev` 依赖没有预编译包，`uv sync` 时会本地编译，需要 C 编译器和内核头文件（Ubuntu：`sudo apt install build-essential linux-libc-dev`）。

## 2. 安装和启动

```shell
uv sync --group dev
PYTHONPATH=src uv run src/zzz_od/gui/app.py
```

- 上面的命令要在仓库根目录执行（脚本路径是相对路径）。
- 项目根目录没有 `.env` 文件时，不要加 `--env-file .env`。
- 也可以用启动脚本 `deploy/linux/zzz-od`：它会自己切到仓库根目录并设置 `PYTHONPATH`，从任何目录、或从应用菜单启动都可以。
  - 终端：`ln -s "$PWD/deploy/linux/zzz-od" ~/bin/zzz-od`（`~/bin` 需要在 `PATH` 里），之后直接运行 `zzz-od`。
  - 应用菜单：在 `~/.local/share/applications/` 新建 `.desktop` 文件，`Exec=` 写这个脚本（或上面软链接）的绝对路径。图标可以用 `assets/ui/logo.ico` 转成的 png。
- 一条龙会通过 `flatpak run moe.launcher.sleepy-launcher --just-run-game` 自动启动游戏，并等待最多约 3 分钟，直到找到游戏窗口。游戏已经在运行时不会重复启动。
  - 一条龙不用玩家的启动器设置启动：每次启动前把 `~/.var/app/moe.launcher.sleepy-launcher/data/sleepy-launcher/config.json` 复制到 `.../data/sleepy-launcher-onedragon/config.json`，把 gamescope 打开、加上环境变量 `ENABLE_GAMESCOPE_WSI=0`（不加的话从 X 截到的画面是全黑的），再用 `flatpak run --env=LAUNCHER_FOLDER=<这个目录> ...` 让启动器读这份配置。配置里的游戏、Wine 前缀、运行器都是绝对路径，所以用的还是同一个游戏；玩家自己的设置不会被改动，启动器退出时写回的配置也只写到这个目录。这次启动的启动器日志 `game.log` / `debug.log` 也在这个目录。
  - `--just-run-game` 与 `--run-game` 相同，区别是只有预下载可用时也会直接启动游戏。游戏需要真正更新时，Sleepy Launcher 会打开自己的窗口，需要手动处理。
  - 启动前会去掉 `SSL_CERT_FILE`、`SSL_CERT_DIR` 和 `NoDefaultCurrentDirectoryInExePath` 这几个环境变量。flatpak 会把环境变量带进沙盒：前两个指向宿主机的证书路径，沙盒里没有，Sleepy Launcher 读不到证书会直接崩溃；第三个是 Windows 变量，Wine 也认，设置后找不到游戏 exe。
  - Sleepy Launcher 的输出写在 `.log/sleepy_launcher.log`，启动失败时先看这里；游戏的 Wine 输出在 `~/.var/app/moe.launcher.sleepy-launcher/data/sleepy-launcher-onedragon/game.log`（一条龙启动时；自己从启动器启动时在 `.../sleepy-launcher/game.log`）。

### 2.1. 代码更新

- Linux 只能从源码运行，用 `git pull` 更新代码，不使用 Windows 的启动器 exe。
- 建议在 `config/env.yml`（不提交）里写：

  ```yaml
  git_remote: upstream
  auto_update_code: false
  ```

  否则一条龙启动时的代码更新可能把 `origin` 远程地址改成官方仓库（对 fork 不友好）。

### 2.2. GPU 推理

- 在「资源管理」里打开闪光识别（`flash_classifier`）和 OCR 的 GPU 选项会快很多：闪光识别每帧 CPU 约 14 ms，CUDA 约 4 ms；整屏 OCR CPU 约 340 ms，CUDA 约 170 ms。
- 启动时 onnxruntime 会打印一条 `/sys/class/drm/card0/device/vendor` 的警告，可以忽略。
- 显存：游戏（DX12，1080p）加上桌面程序已经接近 8G。显存不够时游戏会在切换场景时直接崩溃：游戏的 `Player.log` 最后是 `Async SRP` 相关的报错，内核日志（`journalctl -k`）有 `NVRM: Out of memory`，一条龙随后报「游戏窗口未就绪」。用 `nvidia-smi` 可以看各进程占了多少显存。

## 3. Linux 后端实现

所有 Linux 代码都在单独的文件里，Windows 代码只加了少量 `sys.platform` 分支，方便合并上游。

### 3.1. 文件

| 文件 | 作用 |
|---|---|
| `src/one_dragon/base/controller/gamescope_display.py` | 找到游戏进程、它所在的 X 显示和游戏窗口，建立 X 连接 |
| `src/one_dragon/base/controller/pc_game_window_linux.py` | 游戏窗口（位置、是否有效），接口与 Windows 的 `PcGameWindow` 相同 |
| `src/one_dragon/base/controller/pc_screenshot/xshm_screencapper.py` | 用 X 共享内存（MIT-SHM）截图 |
| `src/one_dragon/base/controller/pc_screenshot/pc_screenshot_controller_linux.py` | 截图控制器，只有一种截图方法 `xshm` |
| `src/one_dragon/base/controller/pc_button/xtest_keyboard_mouse_controller.py` | 用 XTest 发键鼠输入，按键名与 Windows 相同 |
| `src/one_dragon/base/controller/pc_controller_linux.py` | Linux 控制器 `PcControllerLinux`，`ZPcController` 在 Linux 下继承它 |
| `src/one_dragon/base/controller/pc_button/evdev_button_listener.py` | 全局快捷键：读取 `/dev/input` 的键盘鼠标 |

### 3.2. 显示和窗口

- 在 `/proc/*/cmdline` 里找第一个参数是 `ZenlessZoneZero.exe` 的进程（跳过 gamescope 和 steam 的包装进程），从它的 `/proc/<pid>/environ` 读 `DISPLAY`（例如 `:1`，是 gamescope 自带的 Xwayland）。
- 游戏在 flatpak 沙盒里，宿主机上没有这个显示的套接字文件。依次尝试 `/proc/<pid>/root/tmp/.X11-unix/X<n>`、抽象套接字 `@/tmp/.X11-unix/X<n>` 来连接，不需要 X 授权。
- 游戏窗口优先取根窗口属性 `GAMESCOPE_FOCUSED_WINDOW`，再按窗口标题（`ZenlessZoneZero` / `绝区零` / 自定义标题）查找。gamescope 不维护 `_NET_CLIENT_LIST`。
- 每次调用都检查游戏进程还在不在；游戏重启后会重新查找。
- 窗口、截图、输入共用一个 python-xlib 连接。
- **残留会话清理**（`gamescope_display.kill_dead_game_sessions()`）：游戏退出后（正常退出、崩溃、被结束都会出现）Wine 的 `winedevice.exe` 有时不退出，gamescope 要等它启动的进程全部退出才关闭，于是 gamescope 窗口和启动器一直留着，还占显存。Sleepy Launcher 的「结束游戏进程」只 `pkill ZenlessZoneZero`，对这种情况没用。一条龙在启动游戏前（只清理启动超过 1 分钟的会话）和 `close_game` 之后，找出命令行里启动了游戏、但下面已经没有游戏进程的 gamescope，强制结束它下面除 gamescope 以外的进程，gamescope 和启动器随后自己退出。手动清理：`pkill -9 -f winedevice.exe`（只在游戏没在运行时用）。

### 3.3. 截图

- 直接对游戏窗口做 `ShmGetImage`，再转成 RGB（与 Windows 截图结果的颜色顺序相同）。
- 一次完整截图约 2.5 ms。
- 要求游戏进程和一条龙在同一个 IPC 命名空间（Sleepy Launcher 的 flatpak 配置了 `shared=ipc`，满足）。
- 设置里的「截图方法」在 Linux 下隐藏，所有选项都对应 `xshm`。

### 3.4. 键鼠输入

- 用 XTest 把按键和鼠标发到游戏的 X 显示。宿主桌面的焦点在别处也能用，所以 Linux 下**始终是后台运行**，不需要虚拟手柄；「后台模式」开关不影响输入方式。
- 按下和松开之间至少隔 5 ms，否则游戏收不到按键。现在每次按键按住 10 ms（`TAP_HOLD_SECONDS`）。游戏限 60 帧时如果偶尔漏键，可以调大到 0.02。
- 大世界里鼠标被锁定，点击界面要按住 Alt（沿用项目已有的 `pc_alt` 点击方式）。
- 视角转动用 XTest 相对移动。同样的像素数，Linux 下转的角度只有 Windows 的约 1/1.64（Wine 直接传原始移动量，Windows 的 `mouse_event` 会被系统放大）。`PcControllerLinux.mouse_move_relative` 统一乘以 `RELATIVE_MOVE_SCALE = 9.0 / 5.5`，让固定像素的转向与 Windows 一致。
- 文字输入（登录时的账号密码）不走剪贴板，而是逐个字符按键输入。原因：gamescope 会把游戏显示上的剪贴板同步到宿主桌面，密码会留在桌面剪贴板里。当前键盘布局打不出的字符（例如中文）会被跳过。

### 3.5. 全局快捷键

- Wayland 桌面上 pynput 只能收到发给 XWayland 窗口的按键，不能做全局快捷键。
- Linux 下 `PcButtonListener`（开始 / 停止 / 截图等快捷键、按键设置卡片）和开发工具里的快捷键改为直接读取 `/dev/input` 下的键盘鼠标设备。只读不独占，不影响其他程序；后插入的设备约 2 秒内会被发现。
- 按键名与 Windows 下相同（`f9`、`a`、`1`、`numpad_1`、`ctrl_l`、`mouse_x1` 等）。字符键按美式键盘布局的物理位置命名。
- `OneDragonContext.keyboard_controller`（pynput 的 `Controller`）目前没有代码使用，未做改动。

### 3.6. GPU 推理

- `one_dragon/utils/gpu_executor.py` 的 `preload_cuda_dlls()` 在创建 CUDA session 前调用 `onnxruntime.preload_dlls()`，加载 pip 包里的 CUDA / cuDNN 运行库。不调用时 CUDA session 创建失败，onnxruntime 会悄悄退回 CPU。
- OCR 的 CUDA 设置 `cudnn_conv_algo_search` 用 `HEURISTIC`。用 `DEFAULT` 时 cuDNN 9 会刷大量警告，而且比 CPU 还慢。
- OCR 的 CUDA 设置 `arena_extend_strategy` 用 `kSameAsRequested`，并且每次推理都带上运行选项 `memory.enable_memory_arena_shrinkage`（`onnxocr/inference_engine.py` 的 `get_run_options()`），推理结束后把这次多申请的显存还回去。OCR 的输入尺寸不固定，不收缩的话 onnxruntime 的显存池只涨不降，实际运行中一条龙涨到过 2.8G，导致游戏显存不足崩溃。收缩后 OCR 空闲时约占 180MB，每次推理多 3~6 ms。

### 3.7. 声音闪避

- Windows 下录音设备用的是与扬声器 id 相同的回环设备；Linux（PulseAudio / PipeWire）下改为默认输出设备的监听口 `<默认扬声器 id>.monitor`。
- Linux 下录音时指定 `blocksize` 为 10 ms。不指定时 PulseAudio 约 2 秒才交付一次数据，闪避会晚约 2 秒。

### 3.8. 其他 Windows 专用功能的处理

| 功能 | Linux 下 |
|---|---|
| 自动启动游戏 | 运行 Sleepy Launcher，不需要游戏路径；游戏路径设置项隐藏 |
| HDR 开关、启动参数 | 隐藏（写 Windows 注册表 / 用于 Windows 启动游戏） |
| 后台模式设置组、手柄按键设置 | 隐藏；战斗助手和调试页的后台模式开关不再提示「未安装 vgamepad」 |
| 悬浮窗（Overlay）及其设置页 | 不创建、不显示 |
| 复制截图到剪贴板 | 不支持，选项隐藏 |
| 推送设置里复制 cURL 命令 | 用 Qt 剪贴板 |
| 运行结束后关机 / 休眠 / 睡眠 | `shutdown -P +<分钟>`（秒数向上取整到分钟）/ `shutdown -c` 取消 / `systemctl hibernate` / `systemctl suspend` |
| 切换界面语言后重启 | 用当前 Python 重新运行当前脚本 |
| 启动器 exe 的更新检查和下载 | 关闭，启动器下载卡片隐藏 |
| 下载源测速 | `ping -c 1 -W 1` |
| 打包好的启动器（`zzz_od/win_exe/`、`one_dragon/launcher/runtime_launcher.py`、`one_dragon/devtools/python_launcher.py`）、安装器 | 只用于 Windows 打包，Linux 不会用到，未改动 |

## 4. 已验证

- 截图、点击、按键、拖动、滚轮、视角转动、文字输入（在兑换码输入框里验证，未兑换）。
- 日常应用：邮件、丽都周纪、灵敏度校准，通过与 GUI 相同的应用运行入口跑通；活跃度奖励领取了日常奖励，活跃度未满时按设计返回失败。
- 自动战斗（训练场，模板「全配队通用」）：闪光识别后约 10–20 ms 闪避；声音识别比画面识别晚约 200 ms。
- 全局快捷键：用临时的虚拟键盘按 F9，`PcButtonListener`、`ZContext` 的按键事件和开发工具快捷键都能收到。
- 游戏已运行时，「打开游戏」直接进入等待窗口，不会重复启动。

## 5. 未验证 / 已知限制

- **登录**：没有在真正的登录页验证输入账号密码和进入游戏（不能退出游戏）。
- **关闭游戏**（`close_game`，向游戏进程发 SIGTERM，再清理残留会话）、关机 / 休眠 / 睡眠：只检查过拼出的命令，没有实际执行。残留会话清理只用模拟的会话测过。
- **游戏重启后重新连接**：只模拟过。
- 小键盘按键（`numpad_N`）：gamescope 显示上 NumLock 是关的，游戏可能把它们当成方向键等，未验证。
- 依赖手柄的功能（手柄模式战斗助手、手柄灵敏度校准）不可用。
- 部分模型（`lost_void_det`、`hollow_zero_event`）没有在 CUDA 下跑过。
- 只支持一个 gamescope 实例里运行一个游戏；有多个游戏进程时取最新的那个。
- 测试仓 `zzz-od-test` 中没有 Linux 相关的测试。
