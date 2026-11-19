# -*- coding: utf-8 -*-
"""
双击隐藏桌面图标 (EXE 版)
========================
功能：
  1. 双击桌面【空白处】-> 切换桌面图标显隐（双击到图标上不会触发）
  2. 前台应用进入全屏时自动【暂停】双击检测；退出全屏自动【恢复】
  3. 托盘图标：左键双击 = 切换；右键菜单 = 切换/暂停/退出
  4. 单实例运行；启动/退出时自动恢复图标显示（防止异常退出后图标被隐藏）
  5. 运行日志、图标状态文件写入 EXE 所在目录

仅依赖 Python 标准库（ctypes 调用 Win32 API），PyInstaller 单文件打包。
命令行加 --selftest 可自检（控制台模式运行时）。
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
SM_CXICON = 11        # 系统标准图标尺寸（随 DPI 缩放）
SM_CXSMICON = 49      # 系统小图标尺寸（托盘用）
WS_OVERLAPPEDWINDOW = 0x00CF0000
MF_SEPARATOR = 0x00000800
MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
NIIF_INFO = 1

# 隐藏方式：只隐藏桌面图标层 SysListView32，保留 SHELLDLL_DefView 可见。
# 好处：桌面右键菜单仍然可用（菜单由 DefView 提供）；图标显隐可用 IsWindowVisible 查询。
# 注意：Win11 26H2(build 26300) 上经典方案 Progman WM_COMMAND 0x7402 已失效，不要用。
GWL_STYLE = -16
WS_CAPTION = 0x00C00000   # 有标题栏 = 普通窗口，不按全屏处理

# 开机自启（HKCU Run，无管理员权限要求）
HKEY_CURRENT_USER = 0x80000001
KEY_QUERY_VALUE, KEY_SET_VALUE = 0x0001, 0x0002
REG_SZ = 1
RUN_KEY_PATH = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
AUTO_RUN_VALUE = "HideDesktopIconsDblClick"

IDM_TOGGLE, IDM_PAUSE, IDM_EXIT = 1001, 1002, 1003
IDM_BALLOON, IDM_AUTORUN, IDM_LOG = 1004, 1005, 1006
IDM_OPENFOLDER, IDM_ABOUT = 1007, 1008
IDM_OPENINI, IDM_RELOAD = 1009, 1010
TIMER_ID = 1
TIMER_MS = 1000      # 巡检周期 1 秒（够用且省电）

# 切换冷却期：切换后这段时间内忽略新的双击，防止手滑连点导致图标来回闪。
# 可在 设置.ini 里用「冷却时间=毫秒」调整，0 = 关闭冷却。
DEFAULT_COOLDOWN_MS = 250

APP_NAME = "双击隐藏桌面图标"
APP_VERSION = "v1.6"
MB_OK = 0

# 日志轮换：超过 256KB 只保留最后 400 行
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_LINES = 400

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010

MUTEX_NAME = "Local\\HideDesktopIconsDblClick_SingleInstance"
CLASS_NAME = "HideDesktopIconsTrayWnd"

# EXE 所在目录（PyInstaller 冻结后 __file__ 指向临时解压目录，必须用 sys.executable）
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "运行日志.txt")
STATE_PATH = os.path.join(HERE, "图标状态.txt")
SETTINGS_PATH = os.path.join(HERE, "设置.ini")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
advapi32 = ctypes.windll.advapi32

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
user32.ScreenToClient.argtypes = [wt.HWND, POINTER(POINT)]
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
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, c_int, c_int, c_int,
                                  wt.HWND, POINTER(RECT)]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowLongW.argtypes = [wt.HWND, c_int]
user32.GetWindowLongW.restype = ctypes.c_long
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, POINTER(NOTIFYICONDATAW)]
advapi32.RegOpenKeyExW.argtypes = [wt.HKEY, wt.LPCWSTR, wt.DWORD, wt.DWORD, POINTER(wt.HKEY)]
advapi32.RegQueryValueExW.argtypes = [wt.HKEY, wt.LPCWSTR, POINTER(wt.DWORD),
                                      POINTER(wt.DWORD), c_void_p, POINTER(wt.DWORD)]
advapi32.RegSetValueExW.argtypes = [wt.HKEY, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                    c_void_p, wt.DWORD]
advapi32.RegDeleteValueW.argtypes = [wt.HKEY, wt.LPCWSTR]
advapi32.RegCloseKey.argtypes = [wt.HKEY]
user32.LoadIconW.argtypes = [wt.HINSTANCE, c_void_p]
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, c_int, c_int, wt.UINT]
user32.LoadImageW.restype = wt.HICON
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.MessageBoxW.restype = c_int
user32.SetProcessDpiAwarenessContext.argtypes = [c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
user32.SetProcessDPIAware.restype = wt.BOOL
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

# ---------------- 开机自启（注册表 HKCU Run） ----------------
def exe_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)

def autostart_enabled():
    h = wt.HKEY()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                              KEY_QUERY_VALUE, byref(h)) != 0:
        return False
    try:
        return advapi32.RegQueryValueExW(h, AUTO_RUN_VALUE, None, None, None, None) == 0
    finally:
        advapi32.RegCloseKey(h)

def set_autostart(enable):
    h = wt.HKEY()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                              KEY_SET_VALUE, byref(h)) != 0:
        return False
    try:
        if enable:
            data = ('"%s"' % exe_path()).encode("utf-16-le")
            return advapi32.RegSetValueExW(h, AUTO_RUN_VALUE, 0, REG_SZ, data, len(data)) == 0
        ret = advapi32.RegDeleteValueW(h, AUTO_RUN_VALUE)
        return ret == 0 or ret == 2   # 2 = 值不存在，视为已关闭
    finally:
        advapi32.RegCloseKey(h)

# ---------------- 工具函数 ----------------
def load_settings():
    """读取 EXE 同目录的 设置.ini，返回 (是否弹气球通知, 冷却毫秒)。

    冷却时间默认 250ms：0 表示关闭冷却（最灵敏），数值越大越不容易误触。
    """
    show_balloon = True
    cooldown_ms = DEFAULT_COOLDOWN_MS
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(";") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key == "气球通知":
                    show_balloon = val not in ("0", "否", "关", "off", "false", "False")
                elif key == "冷却时间":
                    try:
                        cooldown_ms = max(0, min(2000, int(val)))
                    except ValueError:
                        pass
    except Exception:
        pass
    return show_balloon, cooldown_ms

def save_settings(show_balloon, cooldown_ms):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            f.write("; %s - 设置\n" % APP_NAME)
            f.write("; 改完保存后，在托盘右键菜单点“重新加载设置”即可生效，无需重启程序\n")
            f.write("; 气球通知：1=切换时弹气泡提示  0=静默（托盘文字仍显示状态）\n")
            f.write("; 冷却时间：切换后多少毫秒内忽略新的双击\n")
            f.write(";             0=关闭冷却（最灵敏），250=默认，调大可减少误触\n")
            f.write("气球通知=%d\n" % (1 if show_balloon else 0))
            f.write("冷却时间=%d\n" % cooldown_ms)
    except Exception:
        pass

def app_icon_path():
    base = getattr(sys, "_MEIPASS", None) or HERE
    return os.path.join(base, "app.ico")

def load_app_icon(size):
    """从打包资源加载应用图标；失败则退回系统默认图标。"""
    try:
        h = user32.LoadImageW(None, app_icon_path(), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if h:
            return h
    except Exception:
        pass
    return user32.LoadIconW(None, IDI_APPLICATION)

def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-LOG_KEEP_LINES:])
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(hwnd, buf, 256):
        return buf.value
    return ""

def enable_dpi_awareness():
    """声明 Per-Monitor V2 DPI 感知。

    高缩放（125%/150%）下未声明的进程会被系统位图拉伸，托盘菜单文字发糊；
    声明后系统按原生分辨率渲染，清晰度正常。仅是进程标志，无性能开销。
    """
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return "PerMonitorV2"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "System"
    except Exception:
        pass
    return "Unaware"

# 桌面窗口句柄缓存：避免每次巡检/双击都重复枚举查找（性能优化）
_win_cache = {"dv": None, "lv": None}

def _find_icon_window():
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

def get_icon_window():
    """找到桌面图标层窗口 SHELLDLL_DefView（带缓存）。"""
    dv = _win_cache["dv"]
    if dv and user32.IsWindow(dv):
        return dv
    dv = _find_icon_window()
    _win_cache["dv"] = dv
    if not dv:
        _win_cache["lv"] = None
    return dv

def get_desktop_listview():
    """找到桌面图标列表 SysListView32（带缓存）。"""
    lv = _win_cache["lv"]
    if lv and user32.IsWindow(lv):
        return lv
    w = get_icon_window()
    if not w:
        _win_cache["lv"] = None
        return None
    lv = user32.FindWindowExW(w, None, "SysListView32", None)
    _win_cache["lv"] = lv
    return lv

def is_foreground_fullscreen():
    """前台窗口是否真全屏（无边框覆盖整个显示器）。

    有标题栏（WS_CAPTION）的一律按普通窗口处理，避免任务栏自动隐藏时
    最大化窗口被误判为全屏、导致双击检测被暂停。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    if get_class_name(hwnd) in ("Progman", "WorkerW"):
        return False
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if style & WS_CAPTION:
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

# 命中测试跨进程资源缓存：进程句柄与远程内存块只分配一次并复用（性能优化）
_hit_cache = {"lv": None, "hproc": None, "remote": None}

def _hit_cache_cleanup():
    c = _hit_cache
    if c["remote"] and c["hproc"]:
        try:
            kernel32.VirtualFreeEx(c["hproc"], c["remote"], 0, 0x8000)
        except Exception:
            pass
    if c["hproc"]:
        try:
            kernel32.CloseHandle(c["hproc"])
        except Exception:
            pass
    c["lv"] = c["hproc"] = c["remote"] = None

def _hit_cache_prepare(lv):
    c = _hit_cache
    if c["lv"] == lv and c["hproc"] and c["remote"]:
        return True
    _hit_cache_cleanup()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(lv, byref(pid))
    if not pid.value:
        return False
    hproc = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)
    if not hproc:
        return False
    remote = kernel32.VirtualAllocEx(hproc, None, ctypes.sizeof(LVHITTESTINFO),
                                     0x1000 | 0x2000, 0x04)
    if not remote:
        kernel32.CloseHandle(hproc)
        return False
    c["lv"], c["hproc"], c["remote"] = lv, hproc, remote
    return True

def point_over_icon(pt):
    """远程内存命中测试：判断屏幕点是否落在某个桌面图标上。

    跨进程 SendMessage 直接传本地指针无效（目标进程地址空间不同），
    必须先把结构体写入目标进程内存，再把远程地址作为 LPARAM 传给 LVM_HITTEST。
    """
    lv = get_desktop_listview()
    if not lv:
        return False
    if not _hit_cache_prepare(lv):
        return False
    c = _hit_cache
    cpt = POINT(pt.x, pt.y)
    user32.ScreenToClient(lv, byref(cpt))
    lvi = LVHITTESTINFO()
    lvi.pt = cpt
    lvi.flags = 0xFFFF   # 哨兵：消息未处理则保持原值
    lvi.iItem = -2       # 哨兵
    written = c_size_t()
    if not kernel32.WriteProcessMemory(
            c["hproc"], c["remote"], ctypes.addressof(lvi),
            ctypes.sizeof(LVHITTESTINFO), byref(written)):
        _hit_cache_cleanup()
        return False
    user32.SendMessageW(lv, LVM_HITTEST, 0, c["remote"])
    back = LVHITTESTINFO()
    read = c_size_t()
    if not kernel32.ReadProcessMemory(
            c["hproc"], c["remote"], ctypes.addressof(back),
            ctypes.sizeof(LVHITTESTINFO), byref(read)):
        _hit_cache_cleanup()
        return False
    if back.iItem == -2 and back.flags == 0xFFFF:
        log("命中测试未生效（跨进程写入失败），按空白处理")
        return False
    return (back.iItem != -1) and not (back.flags & LVHT_NOWHERE)

# ---------------- 主逻辑 ----------------
class App:
    DESKTOP_CLASSES = ("Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32")

    def __init__(self):
        self.icons_hidden = False
        self.manual_pause = False
        self.fullscreen_pause = False
        self.show_balloon = True
        self.hwnd = None
        self.hook = None
        self.hook_proc = None
        self._wndproc_ref = None
        self._last_tick = 0
        self._last_pt = POINT(0, 0)
        self._last_icon_hwnd = None
        self._last_toggle_at = 0.0
        self.cooldown_sec = DEFAULT_COOLDOWN_MS / 1000.0

    def apply_settings(self):
        """重新读取 设置.ini 并生效。"""
        self.show_balloon, cd = load_settings()
        self.cooldown_sec = cd / 1000.0
        self.update_tray()
        log("设置已加载：气球通知=%s，冷却=%dms"
            % ("开" if self.show_balloon else "关", cd))

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
        return "%s %s\n%s | %s" % (APP_NAME, APP_VERSION, self.pause_desc(), state)

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
        if not self.hwnd or not self.show_balloon:
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
    def _send_toggle(self):
        """显示/隐藏桌面图标层（SysListView32）。返回是否找到目标窗口。"""
        lv = get_desktop_listview()
        if not lv:
            return False
        user32.ShowWindow(lv, SW_HIDE if not self.icons_hidden else SW_SHOW)
        return True

    def toggle_icons(self):
        if not self._send_toggle():
            log("未找到桌面窗口，无法切换")
            return
        self._last_toggle_at = time.monotonic()   # 开始冷却，防误触
        self.icons_hidden = not self.icons_hidden
        if self.icons_hidden:
            log("切换 -> 隐藏桌面图标")
            self.balloon("桌面图标已隐藏")
        else:
            log("切换 -> 显示桌面图标")
            self.balloon("桌面图标已显示")
        self._save_state()
        self.update_tray()

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                f.write("hidden" if self.icons_hidden else "shown")
        except Exception:
            pass

    def restore_state(self):
        """启动时若上次异常退出时图标被隐藏，尝试恢复显示。

        注意：此切换由资源管理器持久保存，正常退出时已恢复为显示；
        仅当状态文件残留 hidden（异常退出）时才补发一次显示切换。
        """
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = f.read().strip()
        except Exception:
            state = ""
        if state == "hidden":
            lv = get_desktop_listview()
            if lv:
                user32.ShowWindow(lv, SW_SHOW)
            log("上次异常退出时图标处于隐藏状态，已自动恢复显示")
        self.icons_hidden = False

    # ---------- 双击检测 ----------
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
            log("双击忽略：暂停中（%s）" % self.pause_desc())
            return
        if self.cooldown_sec > 0 and time.monotonic() - self._last_toggle_at < self.cooldown_sec:
            log("双击忽略：冷却期（%dms 内的重复触发）" % int(self.cooldown_sec * 1000))
            return
        hwnd = user32.WindowFromPoint(pt)
        cls = get_class_name(hwnd) if hwnd else "?"
        if cls not in self.DESKTOP_CLASSES:
            return  # 点在其他窗口上，属正常情况，不记日志
        if not self.icons_hidden and point_over_icon(pt):
            log("双击忽略：点在桌面图标上 (%d,%d)" % (pt.x, pt.y))
            return
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

    # ---------- 状态自愈 ----------
    def check_icons_state(self):
        """定时校准：explorer 重启或图标窗口句柄变化后，按当前状态重新应用显隐。

        只对可见性做查询比对，不一致才纠正，正常时无任何副作用。
        """
        try:
            lv = get_desktop_listview()
        except Exception:
            return
        if lv and lv != self._last_icon_hwnd:
            if self._last_icon_hwnd:
                log("图标窗口句柄变化（explorer 可能已重启）")
            self._last_icon_hwnd = lv
        if not lv:
            return
        try:
            vis = user32.IsWindowVisible(lv)
        except Exception:
            return
        if self.icons_hidden and vis:
            user32.ShowWindow(lv, SW_HIDE)
            log("状态自愈：图标应隐藏但当前可见，已重新隐藏")
        elif not self.icons_hidden and not vis:
            user32.ShowWindow(lv, SW_SHOW)
            log("状态自愈：图标应显示但当前隐藏，已恢复显示")

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
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.show_balloon else 0),
                           IDM_BALLOON, "气泡通知")
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if autostart_enabled() else 0),
                           IDM_AUTORUN, "开机自启")
        user32.AppendMenuW(menu, MF_STRING, IDM_LOG, "打开日志")
        user32.AppendMenuW(menu, MF_STRING, IDM_OPENINI, "打开设置(编辑)")
        user32.AppendMenuW(menu, MF_STRING, IDM_RELOAD, "重新加载设置")
        user32.AppendMenuW(menu, MF_STRING, IDM_OPENFOLDER, "打开程序目录")
        user32.AppendMenuW(menu, MF_STRING, IDM_ABOUT, "关于")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")
        pt = POINT()
        user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        self.handle_menu(hwnd, cmd)

    def handle_menu(self, hwnd, cmd):
        if cmd == IDM_TOGGLE:
            self.toggle_icons()
        elif cmd == IDM_PAUSE:
            self.manual_pause = not self.manual_pause
            self.update_tray()
        elif cmd == IDM_BALLOON:
            self.show_balloon = not self.show_balloon
            save_settings(self.show_balloon, int(self.cooldown_sec * 1000))
            self.update_tray()
            if self.show_balloon:
                self.balloon("气泡通知已开启")
        elif cmd == IDM_OPENINI:
            try:
                os.startfile(SETTINGS_PATH)
            except Exception:
                pass
        elif cmd == IDM_RELOAD:
            self.apply_settings()
            self.balloon("设置已重新加载（冷却 %dms）" % int(self.cooldown_sec * 1000))
        elif cmd == IDM_AUTORUN:
            enable = not autostart_enabled()
            if set_autostart(enable):
                log("开机自启已%s" % ("开启" if enable else "关闭"))
                self.balloon("开机自启已%s" % ("开启" if enable else "关闭"))
            else:
                log("开机自启设置失败（注册表写入/删除失败）")
                self.balloon("开机自启设置失败")
        elif cmd == IDM_LOG:
            try:
                os.startfile(LOG_PATH)
            except Exception:
                pass
        elif cmd == IDM_OPENFOLDER:
            try:
                os.startfile(HERE)
            except Exception:
                pass
        elif cmd == IDM_ABOUT:
            user32.MessageBoxW(
                hwnd,
                "%s %s\n\n双击桌面空白处切换图标显隐\n全屏应用时自动暂停检测\n\n程序目录：%s\n日志：%s"
                % (APP_NAME, APP_VERSION, HERE, LOG_PATH),
                "关于 %s" % APP_NAME, MB_OK)
        elif cmd == IDM_EXIT:
            user32.DestroyWindow(hwnd)

    def _wnd_proc(self, hwnd, msg, wParam, lParam):
        if msg == WM_APP_TRAY:
            if lParam == WM_RBUTTONUP:
                self.show_menu(hwnd)
            elif lParam == WM_LBUTTONDBLCLK:
                self.toggle_icons()
        elif msg == WM_COMMAND:
            self.handle_menu(hwnd, wParam & 0xFFFF)
        elif msg == WM_TIMER:
            self.check_fullscreen()
            self.check_icons_state()
        elif msg == WM_DESTROY:
            self.cleanup()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wParam, lParam)

    # ---------- 生命周期 ----------
    def run(self):
        self.restore_state()
        self.apply_settings()
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        hicon32 = load_app_icon(user32.GetSystemMetrics(SM_CXICON) or 32)
        hicon16 = load_app_icon(user32.GetSystemMetrics(SM_CXSMICON) or 16)
        wc.hIcon = hicon32
        wc.hIconSm = hicon16
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
        nid.hIcon = hicon32
        nid.szTip = self.tip()[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, byref(nid))

        self.hook_proc = HOOKPROC(self._mouse_hook)
        self.hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self.hook_proc, hinst, 0)
        if not self.hook:
            log("安装全局鼠标钩子失败")
        user32.SetTimer(self.hwnd, TIMER_ID, TIMER_MS, None)
        self._last_icon_hwnd = get_desktop_listview()
        log("已启动 %s：双击桌面空白处可切换图标显隐；全屏时自动暂停检测" % APP_VERSION)
        self.balloon("已在后台运行：双击桌面空白处可切换图标显隐")

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
            _hit_cache_cleanup()
            if self.hwnd:
                user32.KillTimer(self.hwnd, TIMER_ID)
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = self.hwnd
                nid.uID = 1
                shell32.Shell_NotifyIconW(NIM_DELETE, byref(nid))
            if self.icons_hidden:
                lv = get_desktop_listview()
                if lv:
                    user32.ShowWindow(lv, SW_SHOW)
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
    dpi_mode = enable_dpi_awareness()
    log("DPI 感知：%s" % dpi_mode)
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
