import sys
import time

from pynput.keyboard import Controller, Key

from one_dragon.utils.log_utils import log, mask_text

if sys.platform == 'win32':
    import pywintypes
    import win32clipboard
    import win32con
else:
    from one_dragon.base.controller.gamescope_display import GamescopeDisplay
    from one_dragon.base.controller.pc_button.xtest_keyboard_mouse_controller import (
        XTestKeyboardMouseController,
    )


class PcClipboard:

    @staticmethod
    def copy_and_paste(text: str) -> None:
        """
        将给定的文本复制到剪贴板，然后再从剪贴板粘贴出来。

        :param text: 要复制到剪贴板的文本
        :return: 无
        """
        if sys.platform != 'win32':
            PcClipboard.type_on_gamescope(text)
            return
        PcClipboard.copy_string(text)
        PcClipboard.paste_text()
        PcClipboard.empty_clipboard()

    @staticmethod
    def type_on_gamescope(text: str) -> None:
        """
        Linux 下代替复制粘贴 用 XTest 把文本逐字输入到 gamescope 里的游戏。

        不用剪贴板的原因: gamescope 会把游戏显示上的剪贴板同步到宿主桌面的剪贴板
        粘贴账号密码会把它们留在桌面剪贴板里。
        gamescope 里没有输入法 逐字输入不会被输入法干扰。
        当前键盘布局打不出的字符 (例如中文) 会被跳过。

        :param text: 要输入的文本
        :return: 无
        """
        log.info(f'逐字输入文字到游戏:{mask_text(text)}')
        gamescope = GamescopeDisplay()
        try:
            XTestKeyboardMouseController(gamescope).type_text(text)
        finally:
            gamescope.reset()

    @staticmethod
    def empty_clipboard() -> None:
        """
        清空剪贴板的内容。

        :return: 无
        """
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def copy_string(text: str) -> None:
        """
        将给定的文本复制到剪贴板。

        :param text: 要复制到剪贴板的文本
        :return: 无
        """
        try:
            # 脱敏（前后10%的字符不加密）
            log.info(f'复制文字到剪切板:{mask_text(text)}')
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            log.info('复制文字到剪切板成功')
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def paste_text() -> str:
        """
        从剪贴板粘贴文本，并模拟按下 Ctrl+V 组合键。

        :return: 从剪贴板获取的文本，如果获取失败则返回空字符串
        """
        keyboard = Controller()

        try:
            log.info('粘贴文字, 查找剪切板')
            win32clipboard.OpenClipboard()
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            log.info(f'粘贴文字, 获取到:{mask_text(data)}')
        except pywintypes.error:
            data = ''
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        # 使用 pynput 模拟粘贴操作
        log.info('粘贴文字, 按下 Ctrl+V')
        log.debug('粘贴文字, 按下 Ctrl')
        with keyboard.pressed(Key.ctrl):
            time.sleep(0.2)
            log.debug('粘贴文字, 按下 V')
            keyboard.press('v')
            time.sleep(0.2)
            log.debug('粘贴文字, 释放 V')
            keyboard.release('v')

        return data
