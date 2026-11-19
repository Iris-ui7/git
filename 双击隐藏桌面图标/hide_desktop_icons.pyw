# -*- coding: utf-8 -*-
"""
hide_desktop_icons.pyw  ——  双击桌面空白处 隐藏/显示桌面图标（托盘常驻）

功能：
  1. 双击桌面【空白处】-> 切换桌面图标显隐（双击到图标上不会触发）
  2. 前台应用进入全屏时自动【暂停】双击检测；退出全屏自动【恢复】
  3. 托盘图标：左键双击 = 切换；右键菜单 = 切换/暂停/退出
  4. 单实例运行；启动时自动恢复图标显示（防止上次异常退出后图标被隐藏）
  5. 运行日志写入同目录 运行日志.txt

仅依赖 Python 标准库（ctypes 调用 Win32 API）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
from ctypes import (WINFUNCTYPE, byref, POINTER,
                    c_size_t, c_ssize_t, c_int, c_void_p)

# ---------------- Win32 常量 ----------------
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_TIMER = 0x0115
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_APP_TRAY = 0x8001

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x0, 0x1, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10

SW_HIDE, SW_SHOW = 0, 5
IDI_APPLICATION = 32512
MONITOR_DEFAULTTONEAREST = 2
LVM_HITTEST = 0x1012
LVHT_NOWHERE = 0x0001
ERROR_ALREADY_EXISTS = 183
SM_CXDOUBLECLK, SM_CYDOUBLECLK = 36, 38
SM_CXSCREEN, SM_CYSCREEN = 0, 1
WS_OVERLAPPEDWINDOW = 0x00CF0000
MF_SEPARATOR = 0x00000800
MF_STRING = 0x00000000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
NIIF_INFO = 1

IDM_TOGGLE, IDM_PAUSE, IDM_EXIT = 1001, 1002, 1003
TIMER_ID = 1
TIMER_MS = 500

MUTEX_NAME = "Local\\HideDesktopIconsDblClick_SingleInstance"
CLASS_NAME = "HideDesktopIconsTrayWnd"

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "运行日志.txt")
STATE_PATH = os.path.join(HERE, "图标状态.txt")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---------------- 回调函数类型 ----------------
WNDENUMPROC = WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WNDPROC = WINFUNCTYPE(c_ssize_t, wt.HWND, wt.UINT, c_size_t, c_ssize_t)
HOOKPROC = WINFUNCTYPE(c_ssize_t, c_int, c_size_t, c_ssize_t)

# ---------------- 结构体 ----------------
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", c_size_t)]

class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [("pt", POINT), ("flags", wt.UINT), ("iItem", c_int),
                ("iSubItem", c_int), ("iGroup", c_int)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD)]

class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uTimeout", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", wt.BYTE * 16), ("hBalloonIcon", wt.HICON),
    ]

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", c_int), ("cbWndExtra", c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    ]

# ---------------- 函数原型（64 位安全） ----------------
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, c_int]
user32.WindowFromPoint.argtypes = [POINT]
user32.GetWindowRect.argtypes = [wt.HWND, POINTER(RECT)]
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, POINTER(MONITORINFO)]
user32.ShowWindow.argtypes = [wt.HWND, c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, c_size_t, c_ssize_t]
user32.SetWindowsHookExW.argtypes = [c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, c_int, c_size_t, c_ssize_t]
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetDoubleClickTime.argtypes = []
user32.GetSystemMetrics.argtypes = [c_int]
user32.GetCursorPos.argtypes = [POINTER(POINT)]
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, c_int, c_int, c_int,
                                  wt.HWND, POINTER(RECT)]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, POINTER(NOTIFYICONDATAW)]
user32.LoadIconW.argtypes = [wt.HINSTANCE, c_void_p]
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, c_size_t, c_ssize_t]
user32.DefWindowProcW.restype = c_ssize_t
user32.GetMessageW.argtypes = [POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [POINTER(wt.MSG)]
user32.SetTimer.argtypes = [wt.HWND, c_size_t, wt.UINT, c_void_p]
user32.KillTimer.argtypes = [wt.HWND, c_size_t]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostQuitMessage.argtypes = [c_int]
user32.RegisterClassExW.argtypes = [POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   c_int, c_int, c_int, c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, c_void_p]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
kernel32.CreateMutexW.argtypes = [c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.GetLastError.argtypes = []
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.VirtualAllocEx.argtypes = [wt.HANDLE, c_void_p, c_size_t, wt.DWORD, wt.DWORD]
kernel32.VirtualAllocEx.restype = c_void_p
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, c_void_p, c_void_p, c_size_t, POINTER(c_size_t)]
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, c_void_p, c_void_p, c_size_t, POINTER(c_size_t)]
kernel32.VirtualFreeEx.argtypes = [wt.HANDLE, c_void_p, c_size_t, wt.DWORD]

# ---------------- 工具函数 ----------------
def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass

def get_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(hwnd, buf, 256):
        return buf.value
    return ""

def get_icon_window():
    """找到桌面图标层窗口 SHELLDLL_DefView。

    Win10/多数 Win11：它是 WorkerW 的子窗口；部分 Win11：它是 Progman 的直接子窗口。
    """
    progman = user32.FindWindowW("Progman", None)
    if progman:
        h = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if h:
            return h
    hits = []
    @WNDENUMPROC
    def cb(hwnd, lp):
        if get_class_name(hwnd) == "WorkerW":
            h = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if h:
                hits.append(h)
        return True
    user32.EnumWindows(cb, 0)
    if hits:
        return hits[0]
    # 兜底：触发 0x052C 让资源管理器补建后再找
    if progman:
        user32.SendMessageW(progman, 0x052C, 0, 2)
        h = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if h:
            return h
    return None

def get_desktop_listview():
    w = get_icon_window()
    if not w:
        return None
    return user32.FindWindowExW(w, None, "SysListView32", None)

def is_foreground_fullscreen():
    """前台窗口是否覆盖整个显示器（全屏应用）。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    if get_class_name(hwnd) in ("Progman", "WorkerW"):
        return False
    rc = RECT()
    if not user32.GetWindowRect(hwnd, byref(rc)):
        return False
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, byref(mi)):
        return False
    return (rc.left == mi.rcMonitor.left and rc.top == mi.rcMonitor.top
            and rc.right == mi.rcMonitor.right and rc.bottom == mi.rcMonitor.bottom)

def point_over_icon(pt):
    """远程内存命中测试：判断屏幕点是否落在某个桌面图标上。

    跨进程 SendMessage 直接传本地指针无效（目标进程地址空间不同），
    必须先把结构体写入目标进程内存，再把远程地址作为 LPARAM 传给 LVM_HITTEST。
    """
    lv = get_desktop_listview()
    if not lv:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(lv, byref(pid))
    if not pid.value:
        return False
    hproc = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)
    if not hproc:
        return False
    try:
        remote = kernel32.VirtualAllocEx(hproc, None, ctypes.sizeof(LVHITTESTINFO),
                                         0x1000 | 0x2000, 0x04)
        if not remote:
            return False
        try:
            cpt = POINT(pt.x, pt.y)
            user32.ScreenToClient(lv, byref(cpt))
            lvi = LVHITTESTINFO()
            lvi.pt = cpt
            lvi.flags = 0xFFFF   # 哨兵：消息未处理则保持原值
            lvi.iItem = -2       # 哨兵
            written = c_size_t()
            if not kernel32.WriteProcessMemory(
                    hproc, remote, ctypes.addressof(lvi),
                    ctypes.sizeof(LVHITTESTINFO), byref(written)):
                return False
            user32.SendMessageW(lv, LVM_HITTEST, 0, remote)
            back = LVHITTESTINFO()
            read = c_size_t()
            if not kernel32.ReadProcessMemory(
                    hproc, remote, ctypes.addressof(back),
                    ctypes.sizeof(LVHITTESTINFO), byref(read)):
                return False
            if back.iItem == -2 and back.flags == 0xFFFF:
                log("命中测试未生效（跨进程写入失败），按空白处理")
                return False
            return (back.iItem != -1) and not (back.flags & LVHT_NOWHERE)
        finally:
            kernel32.VirtualFreeEx(hproc, remote, 0, 0x8000)
    finally:
        kernel32.CloseHandle(hproc)

# ---------------- 主逻辑 ----------------
class App:
    DESKTOP_CLASSES = ("Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32")

    def __init__(self):
        self.icons_hidden = False
        self.manual_pause = False
        self.fullscreen_pause = False
        self.hwnd = None
        self.hook = None
        self.hook_proc = None
        self._wndproc_ref = None
        self._last_tick = 0
        self._last_pt = POINT(0, 0)

    # ---------- 状态与托盘 ----------
    def paused(self):
        return self.manual_pause or self.fullscreen_pause

    def pause_desc(self):
        if self.manual_pause and self.fullscreen_pause:
            return "已暂停(手动+全屏)"
        if self.manual_pause:
            return "已暂停(手动)"
        if self.fullscreen_pause:
            return "已暂停(全屏中)"
        return "检测中"

    def tip(self):
        state = "图标已隐藏" if self.icons_hidden else "图标显示中"
        return "双击隐藏桌面图标\n%s | %s" % (self.pause_desc(), state)

    def update_tray(self):
        if not self.hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_TIP
        nid.szTip = self.tip()[:127]
        try:
            shell32.Shell_NotifyIconW(NIM_MODIFY, byref(nid))
        except Exception:
            pass

    def balloon(self, text):
        if not self.hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_INFO
        nid.szInfo = text[:255]
        nid.szInfoTitle = "桌面图标"
        nid.dwInfoFlags = NIIF_INFO
        try:
            shell32.Shell_NotifyIconW(NIM_MODIFY, byref(nid))
        except Exception:
            pass

    # ---------- 图标显隐 ----------
    def toggle_icons(self):
        w = get_icon_window()
        if not w:
            log("未找到桌面图标窗口，无法切换")
            return
        if self.icons_hidden:
            user32.ShowWindow(w, SW_SHOW)
            self.icons_hidden = False
            log("切换 -> 显示桌面图标")
            self.balloon("桌面图标已显示")
        else:
            user32.ShowWindow(w, SW_HIDE)
            self.icons_hidden = True
            log("切换 -> 隐藏桌面图标")
            self.balloon("桌面图标已隐藏")
        self._save_state()
        self.update_tray()

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                f.write("hidden" if self.icons_hidden else "shown")
        except Exception:
            pass

    def restore_state(self):
        """启动时确保图标处于显示状态（防止上次异常退出后仍被隐藏）。"""
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = f.read().strip()
        except Exception:
            state = ""
        if state == "hidden":
            w = get_icon_window()
            if w:
                user32.ShowWindow(w, SW_SHOW)
            log("上次退出时图标处于隐藏状态，已自动恢复显示")
        self.icons_hidden = False

    # ---------- 双击检测 ----------
    def is_desktop_blank(self, pt):
        """判断坐标是否落在桌面空白处（排除图标、任务栏、其他窗口）。"""
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return False
        if get_class_name(hwnd) not in self.DESKTOP_CLASSES:
            return False
        if self.icons_hidden:
            # 图标已隐藏时，桌面上任意位置都算空白
            return True
        return not point_over_icon(pt)

    def on_left_down(self, pt, tick):
        dbl_time = user32.GetDoubleClickTime()
        tol_x = user32.GetSystemMetrics(SM_CXDOUBLECLK)
        tol_y = user32.GetSystemMetrics(SM_CYDOUBLECLK)
        is_dbl = False
        if self._last_tick:
            dt = tick - self._last_tick
            if 0 <= dt <= dbl_time and abs(pt.x - self._last_pt.x) <= tol_x \
                    and abs(pt.y - self._last_pt.y) <= tol_y:
                is_dbl = True
        self._last_tick = tick
        self._last_pt = POINT(pt.x, pt.y)
        if not is_dbl:
            return
        self._last_tick = 0  # 消费本次双击，避免三连击重复触发
        if self.paused():
            log("双击桌面：检测暂停中，已忽略")
            return
        if self.is_desktop_blank(pt):
            self.toggle_icons()

    # ---------- 全屏检测 ----------
    def check_fullscreen(self):
        fs = is_foreground_fullscreen()
        if fs and not self.fullscreen_pause:
            self.fullscreen_pause = True
            log("前台应用全屏 -> 暂停双击检测")
            self.balloon("检测已暂停（前台应用全屏）")
            self.update_tray()
        elif not fs and self.fullscreen_pause:
            self.fullscreen_pause = False
            log("应用退出全屏 -> 恢复双击检测")
            self.balloon("检测已恢复")
            self.update_tray()

    # ---------- 窗口与消息 ----------
    def _mouse_hook(self, nCode, wParam, lParam):
        if nCode == 0 and wParam == WM_LBUTTONDOWN:
            info = ctypes.cast(lParam, POINTER(MSLLHOOKSTRUCT))[0]
            try:
                self.on_left_down(info.pt, info.time)
            except Exception as e:
                log("鼠标钩子异常: %r" % e)
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def show_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        pause_text = "恢复检测" if self.manual_pause else "暂停检测"
        user32.AppendMenuW(menu, MF_STRING, IDM_TOGGLE, "切换图标显隐")
        user32.AppendMenuW(menu, MF_STRING, IDM_PAUSE, pause_text)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")
        pt = POINT()
        user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        if cmd == IDM_TOGGLE:
            self.toggle_icons()
        elif cmd == IDM_PAUSE:
            self.manual_pause = not self.manual_pause
            self.update_tray()
        elif cmd == IDM_EXIT:
            user32.DestroyWindow(hwnd)

    def _wnd_proc(self, hwnd, msg, wParam, lParam):
        if msg == WM_APP_TRAY:
            if lParam == WM_RBUTTONUP:
                self.show_menu(hwnd)
            elif lParam == WM_LBUTTONDBLCLK:
                self.toggle_icons()
        elif msg == WM_COMMAND:
            cmd = wParam & 0xFFFF
            if cmd == IDM_TOGGLE:
                self.toggle_icons()
            elif cmd == IDM_PAUSE:
                self.manual_pause = not self.manual_pause
                self.update_tray()
            elif cmd == IDM_EXIT:
                user32.DestroyWindow(hwnd)
        elif msg == WM_TIMER:
            self.check_fullscreen()
        elif msg == WM_DESTROY:
            self.cleanup()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wParam, lParam)

    # ---------- 生命周期 ----------
    def run(self):
        self.restore_state()
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        wc.lpszClassName = CLASS_NAME
        if not user32.RegisterClassExW(byref(wc)):
            log("注册窗口类失败")
            return
        self.hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, "hide_desktop_icons", WS_OVERLAPPEDWINDOW,
            0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            log("创建窗口失败")
            return

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        nid.szTip = self.tip()[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, byref(nid))

        self.hook_proc = HOOKPROC(self._mouse_hook)
        self.hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self.hook_proc, hinst, 0)
        if not self.hook:
            log("安装全局鼠标钩子失败")
        user32.SetTimer(self.hwnd, TIMER_ID, TIMER_MS, None)
        log("已启动：双击桌面空白处可切换图标显隐；全屏时自动暂停检测")

        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

    def cleanup(self):
        try:
            if self.hook:
                user32.UnhookWindowsHookEx(self.hook)
                self.hook = None
            if self.hwnd:
                user32.KillTimer(self.hwnd, TIMER_ID)
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = self.hwnd
                nid.uID = 1
                shell32.Shell_NotifyIconW(NIM_DELETE, byref(nid))
            if self.icons_hidden:
                w = get_icon_window()
                if w:
                    user32.ShowWindow(w, SW_SHOW)
                self.icons_hidden = False
                self._save_state()
                log("退出前自动恢复图标显示")
        except Exception as e:
            log("清理异常: %r" % e)
        log("已退出")

# ---------------- 自检模式 ----------------
def selftest():
    log("===== 自检开始 =====")
    w = get_icon_window()
    log("桌面图标窗口(SHELLDLL_DefView): %s" % w)
    if w:
        user32.ShowWindow(w, SW_HIDE)
        time.sleep(0.8)
        log("隐藏后 IsWindowVisible = %s (期望 False)" % user32.IsWindowVisible(w))
        user32.ShowWindow(w, SW_SHOW)
        time.sleep(0.8)
        log("显示后 IsWindowVisible = %s (期望 True)" % user32.IsWindowVisible(w))
    lv = get_desktop_listview()
    log("桌面列表视图(SysListView32): %s" % lv)
    if lv:
        cx = user32.GetSystemMetrics(SM_CXSCREEN) // 2
        cy = user32.GetSystemMetrics(SM_CYSCREEN) // 2
        over = point_over_icon(POINT(cx, cy))
        log("屏幕中心(%d,%d) 是否在图标上: %s（False=空白，True=图标）" % (cx, cy, over))
    fg = user32.GetForegroundWindow()
    log("当前前台窗口: %s 类名=%s 全屏=%s"
        % (fg, get_class_name(fg), is_foreground_fullscreen()))
    log("===== 自检结束 =====")

def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        log("程序已在运行，本次启动直接退出")
        return
    try:
        App().run()
    finally:
        kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()
