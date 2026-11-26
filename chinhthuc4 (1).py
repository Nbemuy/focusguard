"""Focus Guard: Windows study timer with explicit states and recoverable local data.

Run: py chinhthuc4.py. Only Python's standard library is required on Windows.
"""
from __future__ import annotations

# ==========================================
# PHẦN 1: IMPORT CÁC THƯ VIỆN CẦN THIẾT
# ==========================================
import csv
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ==========================================
# PHẦN 2: CÁC HẰNG SỐ & CẤU HÌNH MẶC ĐỊNH
# ==========================================
BG = "#101827"
CARD = "#1c293c"
FIELD = "#111e30"
TEXT = "#eef4ff"
MUTED = "#a9b9cf"
BLUE = "#6fbdff"
GREEN = "#57d6ac"
AMBER = "#ffd080"
RED = "#ff969b"

# Các thiết lập mặc định ban đầu cho ứng dụng
DEFAULTS = {
    "duration": 25, "break": 5, "goal": 120, "mode": "block",
    "idle_enabled": True, "idle": 5, "grace": 5,
    "allow": ["visual studio code", "word", "excel", "powerpoint", "pdf", "onenote", "notion", "anki", "docs", "bài giảng"],
    "block": ["youtube", "facebook", "tiktok", "netflix", "instagram", "steam", "game", "zalo", "messenger"],
    "sound": True, "popup": True, "strict": False,
}

BROWSER_HINTS = ("chrome", "edge", "firefox", "opera", "brave", "cốc cốc", "coc coc", "safari", "vivaldi")


# ==========================================
# PHẦN 3: CÁC HÀM TIỆN ÍCH HỖ TRỢ (HELPERS)
# ==========================================
def fmt(seconds):
    """Định dạng số giây thành chuỗi thời gian dạng MM:SS."""
    seconds = max(0, math.ceil(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fresh_settings():
    """Trả về một bản sao sâu (deep copy) của cấu hình mặc định."""
    return json.loads(json.dumps(DEFAULTS))


def normalize_settings(raw):
    """Kiểm tra, làm sạch và chuẩn hóa dữ liệu cấu hình đầu vào."""
    result = fresh_settings()
    if not isinstance(raw, dict):
        return result
    for key, low, high in (("duration", 1, 180), ("break", 0, 30), ("goal", 10, 600), ("idle", 1, 60), ("grace", 0, 60)):
        try:
            result[key] = max(low, min(high, int(raw.get(key, result[key]))))
        except (ValueError, TypeError, OverflowError):
            pass
    result["mode"] = "allow" if raw.get("mode") in (0, "allow") else "block"
    for key in ("allow", "block"):
        values = raw.get(key, result[key])
        if isinstance(values, str):
            values = values.split(",")
        if isinstance(values, list):
            result[key] = list(dict.fromkeys(str(v).strip().lower() for v in values if str(v).strip()))
    for key in ("idle_enabled", "sound", "popup", "strict"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    return result


def atomic_json(path, value):
    """Ghi tệp JSON an toàn (atomic write): Ghi ra tệp tạm trước rồi mới thay thế."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as f:
            name = f.name
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def app_name_from_title(title):
    """Rút tên ứng dụng/trang web từ tiêu đề cửa sổ (ví dụ 'Clip - YouTube - Google Chrome' -> 'YouTube')."""
    parts = [p.strip() for p in re.split(r"\s[-–—|]\s", str(title)) if p.strip()]
    if not parts:
        return "Không rõ"
    name = parts[-1]
    if len(parts) >= 2 and any(b in name.lower() for b in BROWSER_HINTS):
        name = parts[-2]
    name = re.sub(r"^\(\d+\)\s*", "", name)
    return name[:30] or "Không rõ"


def record_date(rec):
    """Ngày của một bản ghi lịch sử (hỗ trợ cả bản ghi cũ chưa có trường 'date')."""
    raw = rec.get("date")
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    try:
        return datetime.strptime(rec.get("time", ""), "%d/%m/%Y %H:%M").date()
    except ValueError:
        return None


def compute_streak(dates, today):
    """Số ngày liên tiếp (tính đến hôm nay, hoặc hôm qua nếu hôm nay chưa học) có phiên hoàn thành."""
    day = today if today in dates else today - timedelta(days=1)
    n = 0
    while day in dates:
        n += 1
        day -= timedelta(days=1)
    return n


def longest_streak(dates):
    """Chuỗi ngày liên tiếp dài nhất từng đạt được."""
    best = run = 0
    prev = None
    for d in sorted(dates):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def daily_seconds(records, today, days=7):
    """Tổng số giây học mỗi ngày trong `days` ngày gần nhất (cũ -> mới)."""
    totals = {}
    for r in records:
        d = record_date(r)
        if d is not None:
            totals[d] = totals.get(d, 0) + r["seconds"]
    return [(today - timedelta(days=i), totals.get(today - timedelta(days=i), 0)) for i in range(days - 1, -1, -1)]


def top_apps(records, n=5):
    """Xếp hạng các ứng dụng gây xao nhãng nhiều nhất trong toàn bộ lịch sử."""
    total = {}
    for r in records:
        for name, cnt in r.get("apps", {}).items():
            total[name] = total.get(name, 0) + cnt
    return sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def level_of(score):
    """Xác định nhãn trạng thái và mã màu dựa trên điểm số tập trung hiện tại."""
    if score >= 75:
        return "Tập trung tốt", "#10b981"
    if score >= 40:
        return "Hơi xao nhãng", "#f59e0b"
    return "Mất tập trung nghiêm trọng", "#ef4444"


# ==========================================
# PHẦN 4: CÁC THÀNH PHẦN GIAO DIỆN TÙY CHỈNH (CUSTOM UI)
# ==========================================
class NumberStepper(tk.Frame):
    """Khung điều khiển tăng/giảm giá trị số lượng có hỗ trợ nhập trực tiếp từ bàn phím."""
    def __init__(self, parent, min_val=1, max_val=180, default_val=25, unit_label="phút", step=1):
        super().__init__(parent, bg=CARD)
        self.min_val = min_val
        self.max_val = max_val
        self.value = default_val
        self.step = step

        self.btn_minus = tk.Button(self, text="-", bg="#334155", fg="#f8fafc", activebackground="#475569", activeforeground="white",
                                   bd=0, font=("Segoe UI", 11, "bold"), width=3, command=self.decrease, cursor="hand2")
        self.btn_minus.pack(side="left")

        self.var_val = tk.StringVar(value=str(self.value))
        self.ent_val = tk.Entry(self, textvariable=self.var_val, bg=FIELD, fg=BLUE, 
                                font=("Segoe UI", 11, "bold"), width=5, relief="flat", justify="center",
                                insertbackground="white")
        self.ent_val.pack(side="left", padx=4, ipady=4)
        self.ent_val.bind("<FocusOut>", self.on_entry_focus_out)
        self.ent_val.bind("<Return>", self.on_entry_focus_out)

        self.btn_plus = tk.Button(self, text="+", bg="#334155", fg="#f8fafc", activebackground="#475569", activeforeground="white",
                                  bd=0, font=("Segoe UI", 11, "bold"), width=3, command=self.increase, cursor="hand2")
        self.btn_plus.pack(side="left")

        self.lbl_unit = tk.Label(self, text=unit_label, bg=CARD, fg=MUTED, font=("Segoe UI", 9))
        self.lbl_unit.pack(side="left", padx=(6, 0))

    def increase(self):
        if self.value < self.max_val:
            self.value = min(self.max_val, self.value + self.step)
            self.var_val.set(str(self.value))

    def decrease(self):
        if self.value > self.min_val:
            self.value = max(self.min_val, self.value - self.step)
            self.var_val.set(str(self.value))

    def on_entry_focus_out(self, event=None):
        try:
            val = int(self.var_val.get())
            self.value = max(self.min_val, min(self.max_val, val))
        except ValueError:
            pass
        self.var_val.set(str(self.value))

    def get(self):
        self.on_entry_focus_out()
        return self.value

    def set(self, val):
        self.value = max(self.min_val, min(self.max_val, int(val)))
        self.var_val.set(str(self.value))


# ==========================================
# PHẦN 5: LỚP QUẢN LÝ DỮ LIỆU & LƯU TRỮ (STORE)
# ==========================================
class Store:
    """Quản lý việc đọc/ghi cài đặt, lịch sử và trạng thái chờ (pending)."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.errors = []
        self.blocked = set()
        self.settings = normalize_settings(self.read("focus_settings.json", {}))
        raw = self.read("focus_history.json", [])
        if not isinstance(raw, list):
            self.quarantine("focus_history.json", "Lịch sử không phải danh sách")
            raw = []
        self.records = []
        bad = 0
        for index, record in enumerate(raw):
            try:
                self.records.append(self.clean_record(record, index))
            except (ValueError, TypeError, KeyError, OverflowError):
                bad += 1
        if bad:
            self.quarantine("focus_history.json", f"Có {bad} bản ghi không hợp lệ; đã giữ các bản ghi hợp lệ")
        self.pending = self.read("focus_pending.json", None)

    @staticmethod
    def clean_record(record, index):
        """Kiểm tra tính hợp lệ của từng bản ghi lịch sử."""
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        seconds = float(record.get("seconds", 0))
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid seconds")
        date = record.get("date")
        if not date:
            date = datetime.strptime(record["time"], "%d/%m/%Y %H:%M").strftime("%Y-%m-%d")
        datetime.strptime(date, "%Y-%m-%d")
        daily = record.get("daily", {date: seconds})
        clean_daily = {}
        for day, count in daily.items():
            datetime.strptime(day, "%Y-%m-%d")
            count = float(count)
            if not math.isfinite(count) or count < 0:
                raise ValueError("Invalid daily time")
            clean_daily[day] = count
        if abs(sum(clean_daily.values()) - seconds) > 2:
            raise ValueError("Daily totals mismatch")
        apps = {str(k): max(0, int(v)) for k, v in record.get("apps", {}).items()}
        return dict(record, id=str(record.get("id", f"legacy-{index}-{date}")), date=date,
                    seconds=seconds, daily=clean_daily, apps=apps,
                    score=max(0, min(100, int(record.get("score", 100)))),
                    distractions=max(0, int(record.get("distractions", 0))),
                    completed=bool(record.get("completed", False)))

    def quarantine(self, name, reason):
        """Cô lập tệp dữ liệu bị hỏng bằng cách đổi tên tệp."""
        path = self.directory / name
        backup = path.with_name(path.name + f".broken-{uuid.uuid4().hex[:8]}")
        try:
            path.replace(backup)
            self.errors.append(f"{reason}. Bản gốc được giữ tại {backup}")
        except OSError as exc:
            self.blocked.add(name)
            self.errors.append(f"{reason}. Không thể sao lưu {path}: {exc}. Đã chặn ghi đè tệp này.")

    def read(self, name, default):
        """Đọc tệp JSON an toàn, tự động cô lập nếu tệp bị lỗi."""
        try:
            return json.loads((self.directory / name).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return default
        except (ValueError, UnicodeError):
            self.quarantine(name, "Tệp dữ liệu bị lỗi")
            return default
        except OSError as exc:
            self.blocked.add(name)
            self.errors.append(f"Không đọc được {name}: {exc}. Đã chặn ghi đè.")
            return default

    def write(self, name, data):
        """Ghi dữ liệu vào tệp JSON thông qua atomic write."""
        if name in self.blocked:
            raise OSError(f"Không ghi đè {name} vì chưa đọc/sao lưu được dữ liệu cũ.")
        atomic_json(self.directory / name, data)

    def append(self, record):
        """Thêm một bản ghi mới vào lịch sử."""
        records = [record] + [r for r in self.records if r["id"] != record["id"]]
        self.write("focus_history.json", records)
        self.records = records

    def clear_pending(self):
        """Xóa trạng thái phiên đang chờ (pending)."""
        self.write("focus_pending.json", None)
        self.pending = None


# ==========================================
# PHẦN 6: LỚP QUẢN LÝ PHIÊN HỌC (SESSION)
# ==========================================
class Session:
    """Mô hình quản lý phiên học độc lập với thời gian hệ thống."""
    def __init__(self, settings, wall=None, mono=None):
        self.settings = normalize_settings(settings)
        self.id = uuid.uuid4().hex
        self.total = self.settings["duration"] * 60
        self.seconds = 0.0
        self.daily = {}
        self.score = 100
        self.events = []
        self.apps = {}
        self.state = "running"
        self.reason = ""
        self.calm = 0.0
        self.exceptions = set()
        self.started = time.time() if wall is None else wall
        self.last_wall = self.started
        self.last_mono = time.perf_counter() if mono is None else mono

    def rebase(self, wall=None, mono=None):
        """Cập nhật lại mốc thời gian tường (wall) và thời gian đơn điệu (monotonic)."""
        self.last_wall = time.time() if wall is None else wall
        self.last_mono = time.perf_counter() if mono is None else mono

    def pause(self, reason, penalty=0, app=None):
        """Tạm dừng phiên học và ghi nhận lý do, trừ điểm nếu vi phạm."""
        if self.state != "running":
            return
        self.state, self.reason = "paused", reason
        self.calm = 0
        self.score = max(0, self.score - penalty)
        self.events.append({"time": datetime.now().strftime("%H:%M:%S"), "reason": reason, "penalty": penalty})
        if app:
            self.apps[app] = self.apps.get(app, 0) + 1

    def resume(self, wall=None, mono=None):
        """Tiếp tục phiên học sau khi tạm dừng."""
        if self.state == "paused":
            self.state, self.reason = "running", ""
            self.rebase(wall, mono)

    def advance(self, wall, mono, credit=True):
        """Cập nhật tiến trình thời gian học, phân bổ theo ngày và cộng điểm chuyên cần."""
        delta, wall_delta = mono - self.last_mono, wall - self.last_wall
        previous_wall = self.last_wall
        self.last_wall, self.last_mono = wall, mono
        if self.state != "running":
            return
        if delta < 0 or delta > 5 or wall_delta < 0 or abs(wall_delta - delta) > 2:
            self.pause("Máy ngủ hoặc đồng hồ hệ thống thay đổi. Xác nhận để tiếp tục.")
            return
        if not credit:
            self.calm = 0
            return
        earned = min(delta, max(0, self.total - self.seconds))
        self.seconds += earned
        cursor, remaining = previous_wall, earned
        while remaining > 1e-7:
            dt = datetime.fromtimestamp(cursor)
            midnight = datetime.combine(dt.date() + timedelta(days=1), datetime.min.time()).timestamp()
            part = min(remaining, midnight - cursor)
            day = dt.strftime("%Y-%m-%d")
            self.daily[day] = self.daily.get(day, 0) + part
            cursor += part
            remaining -= part
        self.calm += earned
        while self.calm >= 30:
            self.calm -= 30
            self.score = min(100, self.score + 2)
        if self.seconds >= self.total:
            self.state = "completed"

    def record(self, completed=False):
        """Tạo đối tượng bản ghi kết quả của phiên học."""
        now = datetime.now()
        return {"id": self.id, "time": now.strftime("%d/%m/%Y %H:%M"), "date": now.strftime("%Y-%m-%d"),
                "started": self.started, "seconds": self.seconds, "daily": dict(self.daily),
                "score": self.score, "completed": completed, "apps": dict(self.apps),
                "distractions": sum(e["penalty"] > 0 for e in self.events),
                "reason": "Hoàn thành" if completed else (self.reason or "Kết thúc sớm"), "events": list(self.events)}

    def snapshot(self):
        """Tạo ảnh chụp (snapshot) trạng thái phiên để phục hồi."""
        return {"kind": "session", "settings": self.settings, "record": self.record(self.state == "completed"),
                "total": self.total, "exceptions": sorted(self.exceptions)}

    @classmethod
    def restore(cls, snapshot):
        """Khôi phục phiên học từ dữ liệu snapshot đã lưu."""
        record = Store.clean_record(snapshot["record"], 0)
        session = cls(snapshot["settings"])
        session.id = record["id"]
        session.total = max(60, min(10800, int(snapshot["total"])))
        if record["seconds"] > session.total + 0.001:
            raise ValueError("Thời gian phiên không hợp lệ")
        session.seconds, session.daily, session.score = record["seconds"], record["daily"], record["score"]
        session.apps = record["apps"]
        session.started = record.get("started", session.started)
        session.events = record.get("events", [])
        if not isinstance(session.events, list) or not all(isinstance(e, dict) and isinstance(e.get("penalty"), (int, float)) for e in session.events):
            raise ValueError("Nhật ký phiên không hợp lệ")
        session.exceptions = set(str(t) for t in snapshot.get("exceptions", []))
        session.state = "paused"
        session.reason = "Phiên được khôi phục. Thời gian ứng dụng đóng không được tính."
        return session


# ==========================================
# PHẦN 7: CÁC HÀM TƯƠNG TÁC HỆ THỐNG WINDOWS
# ==========================================
def foreground():
    """Lấy tiêu đề và Process ID của cửa sổ đang active trên Windows."""
    if os.name != "nt":
        return None, None
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    count = user32.GetWindowTextLengthW(hwnd)
    text = ctypes.create_unicode_buffer(count + 1)
    user32.GetWindowTextW(hwnd, text, count + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return text.value.strip() or None, pid.value


def idle_seconds():
    """Đo thời gian hệ thống không có thao tác chuột/bàn phím (Idle time)."""
    if os.name != "nt":
        return None
    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    ctypes.windll.kernel32.GetTickCount.restype = wintypes.DWORD
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def is_distracting(title, pid, settings, exceptions=()):
    """Kiểm tra xem cửa sổ hiện tại có gây xao nhãng hay không dựa theo chế độ cấm/cho phép."""
    if pid == os.getpid():
        return False
    if not title:
        return None
    if title in exceptions:
        return False
    title = title.casefold()
    keys = settings[settings["mode"]]
    match = any(k.casefold() in title for k in keys)
    return not match if settings["mode"] == "allow" else match


def daily_totals(records, session=None):
    """Tính tổng thời gian học theo từng ngày."""
    totals = {}
    for record in records:
        for day, seconds in record["daily"].items():
            totals[day] = totals.get(day, 0) + seconds
    if session:
        for day, seconds in session.daily.items():
            totals[day] = totals.get(day, 0) + seconds
    return totals


def streaks(records):
    """Tính toán chuỗi ngày liên tiếp hoàn thành phiên học và kỷ lục."""
    dates = {datetime.strptime(r["date"], "%Y-%m-%d").date() for r in records if r["completed"]}
    today = datetime.now().date()
    cursor = today if today in dates else today - timedelta(days=1)
    current = 0
    while cursor in dates:
        current += 1
        cursor -= timedelta(days=1)
    best = run = 0
    prev = None
    for day in sorted(dates):
        run = run + 1 if prev and day - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = day
    return current, best


def monitor_rectangles(root):
    """Lấy danh sách kích thước các màn hình hiển thị."""
    rectangles = []
    if os.name == "nt":
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def collect(handle, dc, rect, data):
            r = rect.contents
            if r.right > r.left and r.bottom > r.top:
                rectangles.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return True

        callback = callback_type(collect)
        api = ctypes.windll.user32.EnumDisplayMonitors
        api.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
        api.restype = wintypes.BOOL
        if not api(None, None, callback, 0):
            rectangles.clear()
    return sorted(set(rectangles)) or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


# ==========================================
# PHẦN 8: LỚP GIAO DIỆN CHẾ ĐỘ NGHIÊM NGẶT (STRICT OVERLAY)
# ==========================================
class StrictOverlay:
    """Lớp phủ toàn màn hình khi bật chế độ nghiêm ngặt và phiên bị tạm dừng."""
    def __init__(self, app):
        self.app = app
        self.windows = []
        self.rectangles = []
        self.job = None
        self.closed = False
        self.confirming = False
        self.enforce()

    def build_windows(self, rectangles):
        for window in self.windows:
            window.destroy()
        self.windows = []
        self.rectangles = rectangles
        for rect in rectangles:
            window = tk.Toplevel(self.app.root)
            window.withdraw()
            window.title("Focus Guard • Chế độ nghiêm ngặt")
            window.overrideredirect(True)
            window.configure(bg=BG)
            window.protocol("WM_DELETE_WINDOW", self.ignore_dismiss)
            window.bind("<Alt-F4>", self.ignore_dismiss)
            window.bind("<Escape>", self.ignore_dismiss)
            window.geometry(f"{rect[2]}x{rect[3]}+0+0")
            self.windows.append(window)
        self.draw_content()

    @staticmethod
    def ignore_dismiss(event=None):
        return "break"

    def draw_content(self):
        for window, rect in zip(self.windows, self.rectangles):
            for child in window.winfo_children():
                child.destroy()
            page = ScrollPage(window)
            page.pack(fill="both", expand=True)
            panel = tk.Frame(page.body, bg=BG, padx=20, pady=28)
            panel.pack(fill="x", pady=max(0, min(80, rect[3] // 10)))
            wrap = max(180, min(660, rect[2] - 70))
            self.app.label(panel, "FOCUS GUARD / NGHIÊM NGẶT", 12, BLUE, True, wraplength=wrap).pack(pady=(0, 18))
            self.app.label(panel, "Kết thúc phiên học?" if self.confirming else "Quay lại với việc học", 26, TEXT, True, wraplength=wrap).pack(pady=(0, 12))
            reason = ("Thời gian đã học sẽ được lưu. Màn hình cảnh báo sẽ được gỡ và phiên này kết thúc."
                      if self.confirming else self.app.session.reason)
            self.app.label(panel, reason, 12, AMBER, wraplength=wrap, justify="center").pack(pady=10)
            self.app.label(panel, "Đồng hồ đang tạm dừng. Thời gian này không được tính vào phiên học.",
                           11, MUTED, wraplength=wrap).pack(pady=(0, 18))
            if self.confirming:
                self.app.button(panel, "Xác nhận kết thúc và lưu", lambda: self.app.finish(False), primary=True).pack(pady=6)
                self.app.button(panel, "Quay lại cảnh báo", lambda: self.confirm_end(False)).pack(pady=6)
            else:
                self.app.button(panel, "Tiếp tục học", self.app.main_action, primary=True).pack(pady=6)
                self.app.button(panel, "Kết thúc phiên…", self.confirm_end).pack(pady=6)
                self.app.label(panel, "Sau khi tiếp tục, bạn có 5 giây để trở về tài liệu. Nếu vẫn ở cửa sổ gây xao nhãng, cảnh báo sẽ xuất hiện lại.",
                               10, MUTED, wraplength=wrap).pack(pady=(20, 8))
            self.app.label(panel, "Đây là màn hình nhắc học, không phải khóa bảo mật Windows.",
                           10, MUTED, wraplength=wrap).pack(pady=8)

    def confirm_end(self, value=True):
        if not self.closed:
            self.confirming = value
            self.draw_content()

    def present(self, window, rect):
        if window.state() != "normal":
            window.deiconify()
        window.attributes("-topmost", True)
        window.update_idletasks()
        if os.name == "nt":
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(window.winfo_id(), 2)
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            if not user32.SetWindowPos(hwnd, wintypes.HWND(-1), *rect, 0x0010 | 0x0040):
                raise OSError("Không thể đặt màn hình cảnh báo lên trước.")
        else:
            window.geometry(f"{rect[2]}x{rect[3]}+{rect[0]}+{rect[1]}")
            window.lift()

    def focus_overlay(self):
        focused = self.app.root.focus_displayof()
        if focused is None or focused.winfo_toplevel() not in self.windows:
            self.windows[0].focus_force()

    def enforce(self):
        self.job = None
        if self.closed:
            return
        if not self.app.session or self.app.session.state != "paused":
            self.destroy()
            return
        try:
            rectangles = monitor_rectangles(self.app.root)
            if rectangles != self.rectangles or any(not w.winfo_exists() for w in self.windows):
                self.build_windows(rectangles)
            for window, rect in zip(self.windows, self.rectangles):
                self.present(window, rect)
            self.focus_overlay()
            self.job = self.app.root.after(500, self.enforce)
        except (tk.TclError, OSError) as exc:
            self.destroy()
            self.app.notify(f"Không hiển thị được chế độ nghiêm ngặt: {exc}. Phiên vẫn tạm dừng.")

    def destroy(self):
        self.closed = True
        if self.job:
            self.app.root.after_cancel(self.job)
            self.job = None
        for window in self.windows:
            try:
                window.destroy()
            except tk.TclError:
                pass
        self.windows = []


# ==========================================
# PHẦN 9: LỚP TIỆN ÍCH CUỘN TRANG (SCROLL PAGE)
# ==========================================
class ScrollPage(tk.Frame):
    """Khung trang hỗ trợ thanh cuộn dọc (Scrollbar)."""
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))


# ==========================================
# PHẦN 10: LỚP ỨNG DỤNG CHÍNH (FOCUS GUARD)
# ==========================================
class FocusGuard:
    """Lớp điều khiển chính của ứng dụng Focus Guard."""
    def __init__(self, root, directory=None):
        self.root = root
        root.title("Focus Guard • Học tập có nhịp")
        root.geometry("960x800")
        root.minsize(760, 620)
        root.configure(bg=BG)
        self.store = Store(directory or os.environ.get("FOCUS_GUARD_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FocusGuard")
        self.settings = self.store.settings
        self.session = None
        self.unsaved = None
        self.break_until = None
        self.break_duration = 0
        self.popup = None
        self.strict_overlay = None
        self.away_since = None
        self.offending_title = None
        self.resume_until = 0
        self.last_checkpoint = time.perf_counter()
        self.storage_error = ""
        self.notice_text = ""
        self.notice_until = 0
        self.page = "study"
        self.job = None
        self.sound_thread = None  # luồng đang phát âm cảnh báo (tránh chồng âm)
        self.closing = False
        self.configure_style()
        self.build_shell()
        self.build_study()
        self.build_settings()
        self.build_stats()
        self.load_draft()
        self.show_page("study")
        root.bind_all("<MouseWheel>", self.scroll_page, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.render()
        self.job = root.after(500, self.tick)
        root.after(150, self.startup)

    def configure_style(self):
        """Cấu hình giao diện ttk Style."""
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TProgressbar", background=GREEN, troughcolor=FIELD, borderwidth=0, lightcolor=GREEN, darkcolor=GREEN)
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT, rowheight=32, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#315174")])
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, arrowcolor=TEXT, background=CARD)

    def label(self, parent, text="", size=11, color=TEXT, bold=False, **kwargs):
        """Hàm tạo nhanh một nhãn văn bản (Label)."""
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        """Hàm tạo nhanh một nút bấm (Button)."""
        return tk.Button(parent, text=text, command=command, bg=GREEN if primary else "#2b3d55",
                         fg="#0b2523" if primary else TEXT, activebackground="#83e4c2" if primary else "#3c536f",
                         activeforeground="#0b2523" if primary else TEXT, disabledforeground="#8190a4",
                         relief="flat", bd=0, padx=16, pady=10, cursor="hand2", font=("Segoe UI", 10, "bold"), **kwargs)

    def card(self, parent):
        """Tạo một khung thẻ (Card) giao diện."""
        frame = tk.Frame(parent, bg=CARD, padx=22, pady=18)
        frame.pack(fill="x", padx=22, pady=(0, 16))
        return frame

    def build_shell(self):
        """Xây dựng khung vỏ ứng dụng (Header, Thanh điều hướng, Thanh thông báo lỗi)."""
        header = tk.Frame(self.root, bg=BG, padx=24, pady=16)
        header.pack(fill="x")
        self.label(header, "FOCUS GUARD", 18, BLUE, True).pack(side="left")
        self.mini = self.label(header, "Sẵn sàng cho phiên học mới", 10, MUTED)
        self.mini.pack(side="right")
        nav = tk.Frame(self.root, bg=BG, padx=22)
        nav.pack(fill="x", pady=(0, 14))
        self.nav = {}
        for key, text in (("study", "Học tập"), ("stats", "Thống kê"), ("settings", "Cài đặt")):
            self.nav[key] = self.button(nav, text, lambda k=key: self.show_page(k))
            self.nav[key].pack(side="left", padx=(0, 8))
        self.error_bar = tk.Frame(self.root, bg="#56333c", padx=14, pady=8)
        self.error_label = self.label(self.error_bar, color=TEXT, anchor="w", justify="left", wraplength=600)
        self.error_label.pack(side="left", fill="x", expand=True)
        self.button(self.error_bar, "Thử lưu lại", self.retry_save).pack(side="right")
        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(fill="both", expand=True)
        self.pages = {k: ScrollPage(self.content) for k in ("study", "stats", "settings")}

    def build_study(self):
        """Xây dựng giao diện trang Học tập."""
        body = self.pages["study"].body
        timer = self.card(body)
        self.status = self.label(timer, "SẴN SÀNG", 12, BLUE, True)
        self.status.pack()
        self.clock_label = self.label(timer, fmt(self.settings["duration"] * 60), 64, TEXT, True)
        self.clock_label.pack(pady=(4, 0))
        self.status_detail = self.label(timer, color=MUTED, wraplength=660, justify="center")
        self.status_detail.pack(pady=(0, 16))
        self.progress = ttk.Progressbar(timer, maximum=100)
        self.progress.pack(fill="x", pady=(0, 18))
        actions = tk.Frame(timer, bg=CARD)
        actions.pack()
        self.main_button = self.button(actions, "Bắt đầu học", self.main_action, primary=True, width=20)
        self.main_button.pack(side="left", padx=5)
        self.stop_button = self.button(actions, "Kết thúc phiên", self.stop_confirm, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        self.manual_button = self.button(timer, "Tôi đang bị xao nhãng", self.manual_pause)
        self.manual_button.pack(pady=(12, 0))
        info = self.card(body)
        self.score_label = self.label(info, "Điểm tập trung  —", 14, BLUE, True)
        self.score_label.pack(anchor="w")
        self.score_note = self.label(info, "Điểm tham khảo dựa trên các lần xao nhãng, không phải phép đo khả năng tập trung.", 10, MUTED, wraplength=670, justify="left")
        self.score_note.pack(anchor="w", pady=(5, 0))
        self.current_window = self.label(info, "", 10, MUTED, wraplength=670, justify="left")
        self.current_window.pack(anchor="w", pady=(10, 0))
        self.allow_button = self.button(info, "Đây là nội dung học…", self.allow_current)
        self.goal_card = self.card(body)
        self.label(self.goal_card, "MỤC TIÊU HÔM NAY", 11, MUTED, True).pack(anchor="w")
        self.goal_label = self.label(self.goal_card, "", 18, TEXT, True)
        self.goal_label.pack(anchor="w", pady=6)
        self.goal_bar = ttk.Progressbar(self.goal_card, maximum=100)
        self.goal_bar.pack(fill="x")
        self.streak_label = self.label(self.goal_card, "", 10, MUTED)
        self.streak_label.pack(anchor="w", pady=(8, 0))
        self.notice = self.label(body, "", color=GREEN, wraplength=680, justify="left")
        self.notice.pack(fill="x", padx=24, pady=(0, 18))

    def build_settings(self):
        """Xây dựng giao diện trang Cài đặt (Sử dụng NumberStepper cải tiến)."""
        body = self.pages["settings"].body
        self.label(body, "Thiết lập nhịp học của bạn", 20, TEXT, True).pack(anchor="w", padx=24, pady=(0, 8))
        self.label(body, "Thay đổi chỉ có hiệu lực khi lưu. Quy tắc và thời lượng áp dụng từ phiên kế tiếp.", color=MUTED,
                   wraplength=680, justify="left").pack(anchor="w", padx=24, pady=(0, 16))
        self.vars = {}
        first = self.card(body)
        self.label(first, "Phiên học", 14, BLUE, True).pack(anchor="w", pady=(0, 12))
        
        # Cải tiến: Dùng NumberStepper cho cài đặt thời gian
        self.steppers = {}
        for key, text, low, high, unit, step in (
            ("duration", "Thời lượng học (phút)", 1, 180, "phút", 1),
            ("break", "Nghỉ giải lao (phút; 0 = tắt)", 0, 30, "phút", 1),
            ("goal", "Mục tiêu ngày (phút)", 10, 600, "phút", 5)
        ):
            row = tk.Frame(first, bg=CARD)
            row.pack(fill="x", pady=6)
            self.label(row, text, 10, TEXT, wraplength=470, justify="left").pack(side="left")
            stepper = NumberStepper(row, min_val=low, max_val=high, default_val=self.settings[key], unit_label=unit, step=step)
            stepper.pack(side="right")
            self.steppers[key] = stepper

        detect = self.card(body)
        self.label(detect, "Nhận diện xao nhãng", 14, BLUE, True).pack(anchor="w", pady=(0, 8))
        self.vars["mode"] = tk.StringVar()
        for value, text in (("block", "Danh sách cấm: chỉ cảnh báo tiêu đề có từ khóa đã chọn"), ("allow", "Danh sách học: cảnh báo mọi cửa sổ ngoài danh sách")):
            tk.Radiobutton(detect, text=text, value=value, variable=self.vars["mode"], command=self.keyword_state,
                           bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT, selectcolor=FIELD,
                           font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=2)
        self.label(detect, "Chỉ một danh sách được áp dụng. Từ khóa khớp với tiêu đề cửa sổ, không kiểm tra nội dung trang web.",
                   10, MUTED, wraplength=650, justify="left").pack(anchor="w", pady=(6, 12))
        self.keyword_widgets = {}
        for key, text in (("block", "Từ khóa cấm"), ("allow", "Từ khóa học tập")):
            self.label(detect, text, 11, TEXT, True).pack(anchor="w", pady=(6, 4))
            row = tk.Frame(detect, bg=CARD)
            row.pack(fill="x")
            box = tk.Listbox(row, height=4, bg=FIELD, fg=TEXT, selectbackground="#315174", relief="flat", exportselection=False, font=("Segoe UI", 10))
            box.pack(side="left", fill="x", expand=True)
            scroll = ttk.Scrollbar(row, command=box.yview)
            scroll.pack(side="right", fill="y")
            box.configure(yscrollcommand=scroll.set)
            controls = tk.Frame(detect, bg=CARD)
            controls.pack(fill="x", pady=(6, 8))
            entry = tk.Entry(controls, bg=FIELD, fg=TEXT, disabledbackground=FIELD, disabledforeground=MUTED,
                             insertbackground=TEXT, relief="flat", font=("Segoe UI", 11))
            entry.pack(side="left", fill="x", expand=True, ipady=9)
            add = self.button(controls, "Thêm", lambda k=key: self.add_keyword(k))
            add.pack(side="left", padx=6)
            remove = self.button(controls, "Xóa mục chọn", lambda k=key: self.remove_keyword(k))
            remove.pack(side="left")
            entry.bind("<Return>", lambda e, k=key: self.add_keyword(k))
            self.keyword_widgets[key] = (box, entry, add, remove)
        
        row_grace = tk.Frame(detect, bg=CARD)
        row_grace.pack(fill="x", pady=6)
        self.label(row_grace, "Chờ trước cảnh báo (giây; không tính thời gian ngoài bài học)", 10, TEXT, wraplength=470, justify="left").pack(side="left")
        self.steppers["grace"] = NumberStepper(row_grace, min_val=0, max_val=60, default_val=self.settings["grace"], unit_label="giây", step=1)
        self.steppers["grace"].pack(side="right")

        self.vars["idle_enabled"] = tk.BooleanVar()
        tk.Checkbutton(detect, text="Nhắc khi không thao tác chuột/bàn phím", variable=self.vars["idle_enabled"],
                       bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD, activeforeground=TEXT,
                       command=self.idle_state, font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 4))
        
        row_idle = tk.Frame(detect, bg=CARD)
        row_idle.pack(fill="x", pady=6)
        self.label(row_idle, "Nhắc sau (phút)", 10, TEXT, wraplength=470, justify="left").pack(side="left")
        self.steppers["idle"] = NumberStepper(row_idle, min_val=1, max_val=60, default_val=self.settings["idle"], unit_label="phút", step=1)
        self.steppers["idle"].pack(side="right")
        self.idle_stepper = self.steppers["idle"]

        self.label(detect, "Đọc sách / xem bài giảng? Có thể tắt nhắc không thao tác. Nhắc này không trừ điểm.",
                   10, MUTED, wraplength=650, justify="left").pack(anchor="w", pady=6)
        notify = self.card(body)
        self.label(notify, "Thông báo", 14, BLUE, True).pack(anchor="w")
        for key, text in (("sound", "Âm thanh"), ("popup", "Cửa sổ nhắc thông thường"),
                          ("strict", "Chế độ nghiêm ngặt: phủ màn hình khi tạm dừng")):
            self.vars[key] = tk.BooleanVar()
            tk.Checkbutton(notify, text=text, variable=self.vars[key], bg=CARD, fg=TEXT, selectcolor=FIELD,
                           activebackground=CARD, activeforeground=TEXT, font=("Segoe UI", 11)).pack(anchor="w", pady=5)
        self.label(notify, "Nghiêm ngặt ưu tiên hơn cửa sổ nhắc thông thường: phủ các màn hình, không có nút thu nhỏ/đóng. Bấm Tiếp tục học hoặc xác nhận Kết thúc phiên để gỡ. Có 5 giây để chuyển về tài liệu khi bắt đầu/tiếp tục; sau đó cảnh báo ngay, không dùng mục Chờ trước cảnh báo. Áp dụng từ phiên kế tiếp; không khóa các chức năng hệ thống Windows.",
                   10, MUTED, wraplength=650, justify="left").pack(anchor="w", pady=(8, 0))
        self.label(notify, f"Dữ liệu lưu tại: {self.store.directory}\nLịch sử được giữ đầy đủ, không tự xóa sau 500 phiên.",
                   10, MUTED, wraplength=650, justify="left").pack(anchor="w", pady=(10, 0))
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill="x", padx=22, pady=(0, 24))
        self.button(controls, "Lưu thay đổi", self.save_draft, primary=True).pack(side="left", padx=(0, 8))
        self.button(controls, "Hủy thay đổi", self.cancel_draft).pack(side="left")
        self.button(controls, "Nhập lịch sử cũ…", self.import_history).pack(side="right")

    def keyword_state(self):
        """Cập nhật trạng thái bật/tắt của các ô từ khóa theo chế độ chọn."""
        for key, widgets in self.keyword_widgets.items():
            for widget in widgets:
                widget.configure(state="normal" if key == self.vars["mode"].get() else "disabled")

    def idle_state(self):
        """Cập nhật trạng thái bật/tắt của ô cài đặt thời gian idle."""
        state = "normal" if self.vars["idle_enabled"].get() else "disabled"
        self.idle_stepper.ent_val.configure(state=state)
        self.idle_stepper.btn_plus.configure(state=state)
        self.idle_stepper.btn_minus.configure(state=state)

    def add_keyword(self, key):
        """Thêm từ khóa vào danh sách cấm hoặc cho phép."""
        box, entry, *_ = self.keyword_widgets[key]
        value = entry.get().strip().lower()
        if value and value not in box.get(0, "end"):
            box.insert("end", value)
        entry.delete(0, "end")

    def remove_keyword(self, key):
        """Xóa từ khóa đang chọn khỏi danh sách."""
        box = self.keyword_widgets[key][0]
        for i in reversed(box.curselection()):
            box.delete(i)

    def load_draft(self):
        """Tải dữ liệu cấu hình vào giao diện cài đặt."""
        for key, var in self.vars.items():
            if key in self.settings:
                var.set(self.settings[key])
        for key, stepper in self.steppers.items():
            if key in self.settings:
                stepper.set(self.settings[key])
        for key, (box, entry, *_) in self.keyword_widgets.items():
            box.configure(state="normal")
            box.delete(0, "end")
            for value in self.settings[key]:
                box.insert("end", value)
            entry.configure(state="normal")
            entry.delete(0, "end")
        self.keyword_state()
        self.idle_state()
        self.draft_original = self.draft_signature()

    def draft_signature(self):
        """Lấy chữ ký trạng thái bản nháp cài đặt để kiểm tra thay đổi chưa lưu."""
        return ([v.get() for v in self.vars.values()] + [s.get() for s in self.steppers.values()], 
                [(list(w[0].get(0, "end")), w[1].get()) for w in self.keyword_widgets.values()])

    def save_draft(self):
        """Lưu các thay đổi cài đặt từ giao diện."""
        result = {key: var.get() for key, var in self.vars.items()}
        for key, stepper in self.steppers.items():
            result[key] = stepper.get()
        for key, low, high in (("duration", 1, 180), ("break", 0, 30), ("goal", 10, 600), ("idle", 1, 60), ("grace", 0, 60)):
            try:
                result[key] = int(result[key])
                if not low <= result[key] <= high:
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("Kiểm tra giá trị", f"{ {'duration':'Thời lượng học', 'break':'Nghỉ giải lao', 'goal':'Mục tiêu ngày', 'idle':'Thời gian nhắc', 'grace':'Chờ cảnh báo'}[key]} cần là số nguyên từ {low} đến {high}.", parent=self.root)
                return
        for key, widgets in self.keyword_widgets.items():
            result[key] = list(widgets[0].get(0, "end"))
            pending = widgets[1].get().strip().lower()
            if pending and pending not in result[key]:
                result[key].append(pending)
        if result["mode"] == "allow" and not result["allow"]:
            messagebox.showwarning("Danh sách học đang trống", "Thêm ít nhất một từ khóa học tập hoặc chọn chế độ Danh sách cấm.", parent=self.root)
            return
        try:
            self.store.write("focus_settings.json", result)
        except OSError as exc:
            messagebox.showerror("Chưa lưu cài đặt", f"Thay đổi chưa được áp dụng.\n{exc}", parent=self.root)
            return
        self.settings = self.store.settings = result
        self.load_draft()
        self.notify("Đã lưu cài đặt. Phiên đang diễn ra giữ nguyên quy tắc ban đầu.")
        self.show_page("study")

    def cancel_draft(self):
        """Hủy bỏ các thay đổi cài đặt chưa lưu."""
        self.load_draft()
        self.show_page("study")

    def show_page(self, name):
        """Chuyển đổi hiển thị giữa các trang chức năng (Học tập, Thống kê, Cài đặt)."""
        if self.page == "settings" and name != "settings" and self.draft_signature() != self.draft_original:
            if not messagebox.askyesno("Thay đổi chưa lưu", "Bỏ thay đổi và rời Cài đặt?", parent=self.root):
                return
            self.load_draft()
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self.page = name
        for key, button in self.nav.items():
            button.configure(bg="#315174" if key == name else "#2b3d55")
        if name == "stats":
            self.refresh_stats()
        self.render()

    def scroll_page(self, event):
        """Xử lý sự kiện lăn chuột cho các trang."""
        widget = event.widget
        if isinstance(widget, (tk.Listbox, ttk.Treeview, ttk.Spinbox, NumberStepper)):
            return
        current = widget
        while current is not None and current != self.pages[self.page]:
            current = getattr(current, "master", None)
        if current is not None:
            canvas = self.pages[self.page].canvas
            if canvas.bbox("all") and canvas.bbox("all")[3] > canvas.winfo_height():
                canvas.yview_scroll(-1 * (1 if event.delta > 0 else -1) * 3, "units")

    def build_stats(self):
        """Xây dựng giao diện trang Thống kê chi tiết (tích hợp đầy đủ từ chinhthuc2)."""
        body = self.pages["stats"].body
        self.label(body, "Nhìn lại nhịp học", 20, TEXT, True).pack(anchor="w", padx=24, pady=(0, 16))
        
        # THẺ 1: SỐ LIỆU THỐNG KÊ TỔNG HỢP
        summary = self.card(body)
        self.label(summary, "THỐNG KÊ TỔNG HỢP", 13, BLUE, True).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(summary, bg=CARD)
        grid.pack(fill="x", padx=4, pady=(0, 4))
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="stat")
        self.stat_vals = {}
        self.stat_caps = {}
        items = [
            ("sessions", "Tổng số phiên", BLUE),
            ("completed", "Phiên hoàn thành", GREEN),
            ("time", "Tổng thời gian học", "#a78bfa"),
            ("avg_score", "Điểm tập trung TB", AMBER),
            ("distractions", "Tổng lượt xao nhãng", RED),
            ("avg_dist", "Xao nhãng TB / phiên", "#f97316"),
            ("streak", "Chuỗi ngày liên tiếp", "#fb923c"),
            ("best_score", "Điểm cao nhất", "#22d3ee"),
            ("longest", "Phiên học dài nhất", "#c084fc"),
        ]
        for i, (key, caption, color) in enumerate(items):
            box = tk.Frame(grid, bg=FIELD, highlightbackground="#334155", highlightthickness=1)
            box.grid(row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=4)
            val = tk.Label(box, text="0", font=("Segoe UI", 16, "bold"), bg=FIELD, fg=color)
            val.pack(pady=(8, 0))
            cap = tk.Label(box, text=caption, font=("Segoe UI", 8), bg=FIELD, fg=MUTED)
            cap.pack(pady=(0, 8))
            self.stat_vals[key] = val
            self.stat_caps[key] = cap

        # THẺ 2: THỜI GIAN HỌC 7 NGÀY QUA
        week = self.card(body)
        self.label(week, "Thời gian học trong 7 ngày qua (phút)", 13, BLUE, True).pack(anchor="w")
        self.chart = tk.Canvas(week, height=210, bg=CARD, highlightthickness=0)
        self.chart.pack(fill="x", pady=(10, 0))
        self.chart.bind("<Configure>", lambda e: self.draw_week())

        # THẺ 3: ĐIỂM TẬP TRUNG CÁC PHIÊN GẦN NHẤT
        scores = self.card(body)
        self.label(scores, "Điểm tập trung 10 phiên gần nhất", 13, BLUE, True).pack(anchor="w")
        self.scores_chart = tk.Canvas(scores, height=150, bg=CARD, highlightthickness=0)
        self.scores_chart.pack(fill="x", pady=(10, 0))
        self.scores_chart.bind("<Configure>", lambda e: self.draw_scores())

        # THẺ 4: ỨNG DỤNG GÂY XAO NHÃNG NHIỀU NHẤT
        apps = self.card(body)
        self.label(apps, "Top ứng dụng gây xao nhãng", 13, BLUE, True).pack(anchor="w")
        self.apps_chart = tk.Canvas(apps, height=150, bg=CARD, highlightthickness=0)
        self.apps_chart.pack(fill="x", pady=(10, 0))
        self.apps_chart.bind("<Configure>", lambda e: self.draw_apps_chart())

        # THẺ 5: LỊCH SỬ PHIÊN HỌC (TREEVIEW)
        history = self.card(body)
        self.label(history, "Lịch sử phiên học", 13, BLUE, True).pack(anchor="w", pady=(0, 12))
        table_frame = tk.Frame(history, bg=CARD)
        table_frame.pack(fill="x")
        self.history = ttk.Treeview(table_frame, columns=("time", "duration", "score", "result"), show="headings", height=7)
        for col, title, width in (("time", "Kết thúc", 155), ("duration", "Đã học", 85), ("score", "Điểm", 60), ("result", "Kết quả", 230)):
            self.history.heading(col, text=title)
            self.history.column(col, width=width, minwidth=55)
        self.history.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(table_frame, orient="vertical", command=self.history.yview)
        bar.pack(side="right", fill="y")
        self.history.configure(yscrollcommand=bar.set)
        self.button(history, "Xem chi tiết phiên được chọn", self.record_detail).pack(anchor="w", pady=(10, 0))
        self.history.bind("<Double-1>", lambda e: self.record_detail())

        # THẺ 6: NHẬT KÝ PHIÊN HIỆN TẠI
        log_card = self.card(body)
        self.label(log_card, "Nhật ký phiên hiện tại", 13, BLUE, True).pack(anchor="w")
        self.live_log = self.label(log_card, "Chưa có phiên đang diễn ra.", 10, MUTED, justify="left", wraplength=670)
        self.live_log.pack(anchor="w", pady=(8, 0))

        controls = tk.Frame(body, bg=BG)
        controls.pack(fill="x", padx=22, pady=(0, 24))
        self.button(controls, "Xuất CSV", self.export_csv, primary=True).pack(side="left")
        self.button(controls, "Xóa lịch sử…", self.clear_history).pack(side="right")

    def record_detail(self):
        """Hiển thị chi tiết sự kiện của một phiên học trong lịch sử."""
        selection = self.history.selection()
        if not selection:
            return
        record = self.store.records[int(selection[0])]
        events = record.get("events", [])
        lines = [f"{e.get('time', '')}  {e.get('reason', '')}  (-{e.get('penalty', 0)} điểm)" for e in events]
        window = tk.Toplevel(self.root)
        window.title("Focus Guard • Chi tiết phiên")
        window.geometry("700x450")
        window.configure(bg=BG)
        text = tk.Text(window, wrap="word", bg=FIELD, fg=TEXT, font=("Segoe UI", 11), padx=16, pady=16)
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", f"{record.get('time', '')} • {fmt(record['seconds'])} • {record['score']}/100\n{record.get('reason', '')}\n\n" + ("\n".join(lines) or "Không có sự kiện được lưu."))
        text.configure(state="disabled")

    def visible_records(self):
        """Lấy danh sách các bản ghi hiển thị (bao gồm cả phiên chưa lưu)."""
        records = list(self.store.records)
        if self.unsaved and not any(r["id"] == self.unsaved["id"] for r in records):
            records.insert(0, self.unsaved)
        return records

    def refresh_summary(self):
        """Cập nhật lại các chỉ số tổng hợp trên trang thống kê."""
        records = self.visible_records()
        today = datetime.now().date()
        count = len(records)
        completed = sum(1 for r in records if r["completed"])
        total_seconds = sum(r["seconds"] for r in records)
        total_dist = sum(r["distractions"] for r in records)
        avg_score = round(sum(r["score"] for r in records) / count) if count else 0
        avg_dist = round(total_dist / count, 1) if count else 0
        rate = round(100 * completed / count) if count else 0
        best_score = max((r["score"] for r in records), default=0)
        longest_session = max((r["seconds"] for r in records), default=0)

        done_dates = {d for d in (record_date(r) for r in records if r["completed"]) if d}
        streak = compute_streak(done_dates, today)
        best_streak = longest_streak(done_dates)

        if hasattr(self, "stat_vals"):
            self.stat_vals["sessions"].config(text=str(count))
            self.stat_vals["completed"].config(text=f"{completed}/{count}")
            self.stat_caps["completed"].config(text=f"Phiên hoàn thành ({rate}%)")
            self.stat_vals["time"].config(text=self.fmt_duration(total_seconds))
            self.stat_vals["avg_score"].config(text=f"{avg_score}/100")
            self.stat_vals["distractions"].config(text=str(total_dist))
            self.stat_vals["avg_dist"].config(text=f"{avg_dist:g}")
            self.stat_vals["streak"].config(text=f"{streak} ngày")
            self.stat_caps["streak"].config(text=f"Chuỗi ngày (kỷ lục {best_streak})")
            self.stat_vals["best_score"].config(text=f"{best_score}/100")
            self.stat_vals["longest"].config(text=self.fmt_duration(longest_session))

    def refresh_stats(self):
        """Làm mới toàn bộ số liệu trên trang Thống kê."""
        self.refresh_summary()
        self.history.delete(*self.history.get_children())
        for i, record in enumerate(self.store.records):
            self.history.insert("", "end", iid=str(i), values=(record.get("time", record["date"]), fmt(record["seconds"]), record["score"], "Hoàn thành" if record["completed"] else record.get("reason", "Kết thúc sớm")))
        
        self.update_log()
        self.draw_week()
        self.draw_scores()
        self.draw_apps_chart()

    @staticmethod
    def fmt_duration(sec):
        """Đổi số giây thành chuỗi dễ đọc."""
        sec = max(0, int(sec))
        hours, rem = divmod(sec, 3600)
        minutes = rem // 60
        if hours:
            return f"{hours} giờ {minutes} phút"
        if minutes:
            return f"{minutes} phút"
        return f"{sec} giây"

    def draw_scores(self):
        """Vẽ biểu đồ điểm số của 10 phiên gần nhất."""
        canvas = self.scores_chart
        canvas.delete("all")
        records = list(reversed(self.visible_records()[:10]))
        width = canvas.winfo_width()
        if width < 50:
            return
        if not records:
            canvas.create_text(20, 50, anchor="w", text="Hoàn thành một phiên để xem điểm theo thời gian.", fill=MUTED, font=("Segoe UI", 10))
            return
        slot = (width - 40) / len(records)
        for i, record in enumerate(records):
            x = 20 + (i + .5) * slot
            top = 110 - record["score"] * .8
            color = GREEN if record["score"] >= 75 else AMBER if record["score"] >= 40 else RED
            canvas.create_rectangle(x - min(18, slot * .3), min(top, 109), x + min(18, slot * .3), 110, fill=color, outline="")
            canvas.create_text(x, top - 12, text=str(record["score"]), fill=TEXT, font=("Segoe UI", 10))
            canvas.create_text(x, 130, text=record["date"][8:] + "/" + record["date"][5:7], fill=MUTED, font=("Segoe UI", 9))

    def update_log(self):
        """Cập nhật nhật ký sự kiện phiên hiện tại."""
        events = self.session.events if self.session else []
        self.live_log.configure(text="\n".join(f"{e['time']}  {e['reason']}  (-{e['penalty']} điểm)" for e in events[-10:]) or ("Chưa ghi nhận sự kiện." if self.session else "Chưa có phiên đang diễn ra."))

    def draw_week(self):
        """Vẽ biểu đồ thời gian học trong 7 ngày gần nhất."""
        canvas = self.chart
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 50:
            return
        totals = daily_totals(self.visible_records(), self.session)
        today = datetime.now().date()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        values = [totals.get(d.isoformat(), 0) / 60 for d in days]
        maximum = max(self.settings["goal"], max(values), 1) * 1.2
        left, bottom, height = 42, 166, 125
        slot = (width - 65) / 7
        y = bottom - self.settings["goal"] / maximum * height
        canvas.create_line(left, y, width - 12, y, fill=AMBER, dash=(4, 4))
        canvas.create_text(left, y - 10, text=f"Mục tiêu {self.settings['goal']} phút", anchor="w", fill=AMBER, font=("Segoe UI", 9))
        names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        for i, (day, value) in enumerate(zip(days, values)):
            x = left + (i + .5) * slot
            top = bottom - value / maximum * height
            canvas.create_rectangle(x - 16, min(top, bottom - 2), x + 16, bottom, fill=GREEN if value >= self.settings["goal"] else BLUE, outline="")
            canvas.create_text(x, top - 12, text=f"{value:.1f}", fill=TEXT, font=("Segoe UI", 10))
            canvas.create_text(x, bottom + 20, text=f"{names[day.weekday()]} {day.strftime('%d/%m')}", fill=MUTED, font=("Segoe UI", 9), anchor="n", justify="center")

    def draw_apps_chart(self):
        """Vẽ biểu đồ thanh ngang cho các ứng dụng gây xao nhãng nhiều nhất."""
        c = getattr(self, "apps_chart", None)
        if c is None:
            return
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 60 or h < 60:
            return
        apps = top_apps(self.visible_records(), 5)
        if not apps:
            c.create_text(w / 2, h / 2, fill=MUTED, font=("Segoe UI", 10), anchor="center",
                          text="Chưa có dữ liệu xao nhãng.")
            return
        colors = ["#ef4444", "#f97316", "#f59e0b", "#a78bfa", "#38bdf8"]
        left, right, top, row_h = 180, 60, 8, 26
        biggest = max(cnt for _, cnt in apps)
        for i, (name, cnt) in enumerate(apps):
            y = top + i * row_h
            c.create_text(8, y + row_h / 2, text=name, anchor="w", fill=TEXT, font=("Segoe UI", 9))
            length = max(4, (w - left - right) * cnt / biggest)
            c.create_rectangle(left, y + 5, left + length, y + row_h - 5, fill=colors[i % len(colors)], outline="")
            c.create_text(left + length + 6, y + row_h / 2, text=f"{cnt} lần", anchor="w",
                          fill=MUTED, font=("Segoe UI", 8))

    def startup(self):
        """Xử lý khi ứng dụng khởi động, kiểm tra dữ liệu cũ hoặc khôi phục phiên chưa hoàn thành."""
        if self.store.errors:
            messagebox.showwarning("Kiểm tra dữ liệu", "\n\n".join(self.store.errors), parent=self.root)
        pending = self.store.pending
        if not pending:
            return
        try:
            record = Store.clean_record(pending["record"], 0)
            if any(r["id"] == record["id"] for r in self.store.records):
                self.store.clear_pending()
                return
            if pending.get("kind") == "record":
                self.unsaved = record
                self.retry_save()
                return
            recovered = Session.restore(pending)
            self.session = recovered
            if record["completed"] or recovered.seconds >= recovered.total:
                self.finish(True)
            else:
                self.notify("Đã khôi phục phiên trước ở trạng thái tạm dừng. Bấm Tiếp tục hoặc Kết thúc phiên.")
                if recovered.settings["strict"]:
                    self.show_popup()
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.store.quarantine("focus_pending.json", f"Không khôi phục được phiên: {exc}")
            messagebox.showwarning("Phiên phục hồi bị lỗi", self.store.errors[-1], parent=self.root)
        except OSError as exc:
            self.set_storage_error(str(exc))
        self.render()

    def main_action(self, bring_front=True):
        """Xử lý hành động chính của nút (Bắt đầu học / Tiếp tục / Thử lưu lại).

        bring_front: khi tiếp tục học, đưa cửa sổ Focus Guard lên trước (giống chinhthuc2)."""
        if self.unsaved:
            self.retry_save()
            return
        if not self.session:
            if self.break_until:
                self.break_until = None
            self.session = Session(self.settings)
            self.offending_title = None
            self.away_since = None
            self.resume_until = time.perf_counter() + 5
            self.checkpoint()
        elif self.session.state == "running":
            self.session.pause("Bạn đã tạm dừng phiên học.")
            self.checkpoint()
            if self.session.settings["strict"]:
                self.show_popup()
        else:
            if bring_front:
                # Làm TRƯỚC khi đóng cảnh báo: lúc này Focus Guard còn giữ quyền foreground nên Windows cho phép đưa lên trước.
                self.return_to_app()
            self.close_popup(force=True)
            self.session.resume()
            self.away_since = None
            self.resume_until = time.perf_counter() + 5
            self.checkpoint()
        self.render()

    def return_to_app(self):
        """Đưa cửa sổ Focus Guard lên trước và về trang Học tập (giống chinhthuc2 khi bấm "Quay lại học ngay")."""
        try:
            if self.page == "stats":
                self.show_page("study")
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)  # thủ thuật đảm bảo cửa sổ nổi lên trên các ứng dụng khác
            self.root.focus_force()
            self.root.after(250, self.release_topmost)
        except tk.TclError:
            pass

    def release_topmost(self):
        """Bỏ chế độ luôn-trên-cùng sau khi đã đưa cửa sổ lên trước."""
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def manual_pause(self):
        """Xử lý khi người dùng bấm nút 'Tôi đang bị xao nhãng'."""
        if self.session and self.session.state == "running":
            self.session.pause("Bạn tự đánh dấu bị xao nhãng.", 5)
            self.checkpoint()
            self.play_sound()
            if self.session.settings["strict"]:
                self.show_popup()
            self.render()

    def stop_confirm(self):
        """Xác nhận kết thúc phiên học sớm."""
        if not self.session:
            return
        if self.strict_overlay and not self.strict_overlay.closed:
            self.strict_overlay.confirm_end()
            return
        was_running = self.session.state == "running"
        if was_running:
            self.session.pause("Kết thúc sớm")
        if messagebox.askyesno("Kết thúc phiên?", "Lưu thời gian đã học và kết thúc phiên này?\nBạn có thể chọn Không để tiếp tục.", parent=self.root):
            self.finish(False)
        elif was_running:
            self.session.resume()
        self.render()

    def finish(self, completed):
        """Hoàn thành hoặc kết thúc phiên học và tiến hành lưu kết quả."""
        if not self.session:
            return
        session = self.session
        record = session.record(completed)
        self.close_popup(force=True)
        self.unsaved = record
        self.session = None
        self.offending_title = None
        try:
            self.store.write("focus_pending.json", {"kind": "record", "record": record})
        except OSError:
            pass
        if not self.retry_save():
            self.render()
            return
        self.notify(f"{'Hoàn thành' if completed else 'Đã lưu'} phiên • {fmt(record['seconds'])} đã học • {record['score']}/100 điểm.")
        if completed:
            self.play_sound(success=True)
            if session.settings["break"]:
                self.break_duration = session.settings["break"] * 60
                self.break_until = time.perf_counter() + self.break_duration
        self.render()

    def checkpoint(self):
        """Lưu điểm kiểm tra định kỳ (checkpoint) trạng thái phiên hiện tại."""
        if not self.session:
            return
        try:
            self.store.write("focus_pending.json", self.session.snapshot())
            self.set_storage_error("")
        except OSError as exc:
            self.set_storage_error(f"Chưa lưu được phiên dự phòng: {exc}. Giữ ứng dụng mở và thử lưu lại.")
        self.last_checkpoint = time.perf_counter()

    def retry_save(self):
        """Thử lưu lại các dữ liệu hoặc phiên chưa được ghi xuống đĩa."""
        try:
            if self.unsaved:
                self.store.append(self.unsaved)
                self.unsaved = None
                self.store.clear_pending()
                self.notify("Đã lưu phiên học thành công.")
            elif self.session:
                self.store.write("focus_pending.json", self.session.snapshot())
            else:
                self.store.clear_pending()
            self.set_storage_error("")
            if self.page == "stats":
                self.refresh_stats()
            self.render()
            return True
        except OSError as exc:
            self.set_storage_error(f"Chưa lưu được dữ liệu: {exc}. Có thể xuất CSV trong Thống kê; không đóng ứng dụng trước khi lưu.")
            return False

    def set_storage_error(self, text):
        """Hiển thị hoặc ẩn thông báo lỗi lưu trữ trên thanh giao diện."""
        self.storage_error = text
        self.error_label.configure(text=text)
        if text:
            self.error_bar.pack(fill="x", before=self.content, padx=22, pady=(0, 12))
        else:
            self.error_bar.pack_forget()

    def notify(self, text):
        """Hiển thị thông báo ngắn trên giao diện."""
        self.notice_text = text
        self.notice_until = time.perf_counter() + 20

    def play_sound(self, success=False):
        """Phát âm thanh cảnh báo giống chinhthuc2: hai tiếng Beep trên luồng riêng để không làm đơ giao diện."""
        settings = self.session.settings if self.session else self.settings
        if not settings["sound"]:
            return
        if os.name == "nt":
            if self.sound_thread is not None and self.sound_thread.is_alive():
                return  # âm cảnh báo trước vẫn đang phát
            import winsound
            tones = ((660, 200), (880, 300)) if success else ((880, 350), (660, 350))

            def worker():
                try:
                    for frequency, duration in tones:
                        winsound.Beep(frequency, duration)
                except (RuntimeError, OSError):
                    try:  # máy không có thiết bị Beep: dùng âm thanh hệ thống thay thế
                        winsound.MessageBeep(winsound.MB_OK if success else winsound.MB_ICONEXCLAMATION)
                    except (RuntimeError, OSError):
                        pass

            self.sound_thread = threading.Thread(target=worker, daemon=True)
            self.sound_thread.start()
        else:
            try:
                self.root.bell()
            except tk.TclError:
                pass

    def show_popup(self):
        """Hiển thị cửa sổ popup nhắc nhở khi phiên tạm dừng."""
        if not self.session or self.session.state != "paused":
            return
        if self.session.settings["strict"]:
            if not self.strict_overlay or self.strict_overlay.closed:
                self.close_popup(force=True)
                self.strict_overlay = StrictOverlay(self)
            return
        if self.popup or not self.session.settings["popup"]:
            return
        window = tk.Toplevel(self.root)
        self.popup = window
        window.title("Focus Guard • Phiên học tạm dừng")
        window.geometry("580x340")
        window.minsize(540, 320)
        window.configure(bg=CARD)
        self.label(window, "Phiên học đang tạm dừng", 20, AMBER, True).pack(padx=20, pady=(24, 12))
        self.label(window, self.session.reason, 11, TEXT, wraplength=510, justify="center").pack(padx=20, pady=8)
        self.label(window, "Thời gian tạm dừng không được tính. Đóng cửa sổ này vẫn giữ phiên tạm dừng.", 10, MUTED, wraplength=500).pack(padx=20, pady=10)
        self.button(window, "Tiếp tục học", self.main_action, primary=True).pack(pady=6)
        if self.offending_title:
            self.button(window, "Đây là nội dung học…", self.allow_current).pack(pady=4)
        self.button(window, "Kết thúc phiên…", self.stop_confirm).pack(pady=4)
        window.protocol("WM_DELETE_WINDOW", self.close_popup)

    def close_popup(self, force=False):
        """Đóng các cửa sổ popup hoặc overlay cảnh báo."""
        if self.strict_overlay:
            if not force and not self.strict_overlay.closed:
                return
            self.strict_overlay.destroy()
            self.strict_overlay = None
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def allow_current(self):
        """Thêm tiêu đề cửa sổ hiện tại vào danh sách ngoại lệ được phép học."""
        if self.strict_overlay and not self.strict_overlay.closed:
            return
        if not self.session or not self.offending_title:
            return
        title = self.offending_title
        if messagebox.askyesno("Cho phép nội dung học", f"Cho phép chính xác tiêu đề này trong phiên hiện tại?\n\n{title}\n\nKhông cho phép toàn bộ trình duyệt. Tiêu đề thay đổi sẽ được kiểm tra lại.", parent=self.popup or self.root):
            self.session.exceptions.add(title)
            self.offending_title = None
            if self.session.state == "paused":
                self.main_action(bring_front=False)
            self.checkpoint()
            self.render()

    def monitor(self, wall, mono):
        """Giám sát trạng thái cửa sổ và thời gian rảnh rỗi (idle) của người dùng."""
        session = self.session
        title, pid = foreground()
        state = is_distracting(title, pid, session.settings, session.exceptions)
        self.current_window.configure(text="Cửa sổ hiện tại: " + (title[:110] if title else "Chưa đọc được"))
        if session.state != "running":
            session.rebase(wall, mono)
            return
        idle = idle_seconds() if session.settings["idle_enabled"] else 0
        previous_state = session.state
        delta = mono - session.last_mono
        gap = delta < 0 or delta > 5 or wall - session.last_wall < 0 or abs(wall - session.last_wall - delta) > 2
        if gap:
            session.advance(wall, mono, credit=False)
        elif state is None:
            session.pause("Không đọc được cửa sổ hiện tại. Kiểm tra cửa sổ học rồi bấm Tiếp tục.")
        elif session.settings["idle_enabled"] and idle is None:
            session.pause("Không đọc được trạng thái thao tác. Kiểm tra hệ thống hoặc tắt nhắc không thao tác cho phiên sau.")
        elif session.settings["idle_enabled"] and idle > session.settings["idle"] * 60:
            session.pause("Bạn còn đang học không? Đã lâu chưa có thao tác. Nhắc này không trừ điểm.")
        elif state:
            self.offending_title = title
            name = app_name_from_title(title)
            session.apps[name] = session.apps.get(name, 0) + 1
            if mono < self.resume_until:
                session.advance(wall, mono, credit=False)
            else:
                if self.away_since is None:
                    self.away_since = mono
                session.advance(wall, mono, credit=False)
                if mono - self.away_since >= (0 if session.settings["strict"] else session.settings["grace"]):
                    session.pause(f"Cửa sổ ngoài bài học: {title[:100]}", 8, title)
        else:
            self.away_since = None
            self.offending_title = None
            session.advance(wall, mono)
        if previous_state == "running" and session.state == "paused":
            self.checkpoint()
            self.play_sound()
            self.show_popup()
        if session.state == "completed":
            self.finish(True)

    def tick(self):
        """Hàm vòng lặp hệ thống định kỳ (mỗi 500ms)."""
        if self.closing:
            return
        try:
            mono, wall = time.perf_counter(), time.time()
            if self.session:
                self.monitor(wall, mono)
                if self.session and mono - self.last_checkpoint >= 10:
                    self.checkpoint()
            elif self.break_until and mono >= self.break_until:
                self.break_until = None
                self.notify("Đã hết giờ nghỉ. Bắt đầu phiên mới khi bạn sẵn sàng.")
                self.play_sound(success=True)
            self.render()
            if self.page == "stats":
                self.refresh_summary()
                self.update_log()
                self.draw_week()
        except Exception as exc:
            if self.session:
                self.session.pause("Lỗi giám sát. Phiên đã tạm dừng để tránh tính sai thời gian.")
            self.set_storage_error(f"Lỗi khi cập nhật: {exc}. Phiên đã được tạm dừng.")
            self.render()
        finally:
            if not self.closing:
                self.job = self.root.after(500, self.tick)

    def render(self):
        """Cập nhật giao diện hiển thị theo trạng thái phiên học hiện tại."""
        if not hasattr(self, "clock_label"):
            return
        session = self.session
        if session:
            remaining = session.total - session.seconds
            paused = session.state == "paused"
            checking = not paused and self.offending_title is not None
            status = "ĐANG TẠM DỪNG" if paused else ("ĐANG Ở NGOÀI BÀI HỌC" if checking else "Đang HỌC")
            detail = session.reason if paused else ("Chuyển về tài liệu học. Thời gian ngoài bài học chưa được tính." if checking else "Từng phút tập trung đều có ý nghĩa.")
            color = AMBER if paused or checking else GREEN
            self.main_button.configure(text="Tiếp tục học" if paused else "Tạm dừng", state="normal")
            self.stop_button.configure(state="normal")
            self.manual_button.configure(state="disabled" if paused else "normal")
            self.score_label.configure(text=f"Điểm tập trung  {session.score}/100", fg=GREEN if session.score >= 75 else AMBER if session.score >= 40 else RED)
            if session.events:
                event = session.events[-1]
                self.score_note.configure(text=f"Gần nhất: {event['reason']} (-{event['penalty']} điểm). Tập trung liên tục 30 giây: +2 điểm.")
            else:
                self.score_note.configure(text="Điểm tham khảo: đổi sang cửa sổ xao nhãng -8; tự báo -5; nhắc không thao tác không trừ điểm.")
            self.progress["value"] = session.seconds / session.total * 100
        else:
            resting = self.break_until is not None
            remaining = max(0, self.break_until - time.perf_counter()) if resting else self.settings["duration"] * 60
            status = "ĐANG NGHỈ GIẢI LAO" if resting else "SẴN SÀNG"
            detail = "Đứng dậy, uống nước và cho mắt nghỉ ngơi." if resting else f"Phiên {self.settings['duration']} phút • Nghỉ {self.settings['break']} phút • Thiết lập trong Cài đặt"
            color = BLUE
            if self.unsaved:
                status, detail, color = "PHIÊN CHƯA LƯU", "Lưu lại phiên trước khi bắt đầu phiên mới.", RED
            self.main_button.configure(text="Thử lưu lại" if self.unsaved else "Học tiếp (bỏ qua nghỉ)" if resting else "Bắt đầu học", state="normal")
            self.stop_button.configure(state="disabled")
            self.manual_button.configure(state="disabled")
            self.progress["value"] = 100 * (1 - remaining / self.break_duration) if resting else 0
            latest = self.unsaved or (self.store.records[0] if self.store.records else None)
            self.score_label.configure(text=f"Điểm phiên gần nhất  {latest['score']}/100" if latest else "Điểm tập trung  —", fg=BLUE)
            self.score_note.configure(text="Điểm chỉ phản ánh các quy tắc của ứng dụng, không đo khả năng tập trung thực tế.")
            self.current_window.configure(text="")
        self.status.configure(text=status, fg=color)
        self.status_detail.configure(text=detail)
        self.clock_label.configure(text=fmt(remaining))
        self.mini.configure(text=f"{status.capitalize()} · {fmt(remaining)}", fg=color)
        if self.offending_title and session:
            self.allow_button.pack(anchor="w", pady=(10, 0))
        else:
            self.allow_button.pack_forget()
        totals = daily_totals(self.visible_records(), session)
        today = totals.get(datetime.now().strftime("%Y-%m-%d"), 0) / 60
        self.goal_label.configure(text=f"{today:.1f} / {self.settings['goal']} phút" + ("  •  Đã đạt mục tiêu" if today >= self.settings["goal"] else ""))
        self.goal_bar["value"] = min(100, today / self.settings["goal"] * 100)
        current, best = streaks(self.visible_records())
        self.streak_label.configure(text=f"{current} ngày liên tiếp hoàn thành phiên • Kỷ lục {best} ngày")
        self.notice.configure(text=self.notice_text if time.perf_counter() < self.notice_until else "")

    def export_csv(self):
        """Xuất lịch sử phiên học ra tệp CSV."""
        records = self.visible_records()
        if not records:
            messagebox.showinfo("Xuất CSV", "Chưa có phiên kết thúc để xuất.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv", initialfile=f"focus_guard_{datetime.now():%Y%m%d}.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        def safe(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Thời gian", "Giây học", "Điểm", "Hoàn thành", "Lý do", "Cửa sổ xao nhãng", "Phân bổ theo ngày", "Đã lưu lịch sử"])
                for r in records:
                    writer.writerow([r.get("time", r["date"]), round(r["seconds"], 2), r["score"], "Có" if r["completed"] else "Không", safe(r.get("reason", "")), safe(json.dumps(r["apps"], ensure_ascii=False)), json.dumps(r["daily"], ensure_ascii=False), "Không" if self.unsaved and r["id"] == self.unsaved["id"] else "Có"])
            messagebox.showinfo("Đã xuất", f"Đã xuất {len(records)} phiên.\n{path}", parent=self.root)
        except OSError as exc:
            messagebox.showerror("Không xuất được", str(exc), parent=self.root)

    def clear_history(self):
        """Xóa toàn bộ lịch sử phiên học."""
        if self.session or self.unsaved:
            messagebox.showinfo("Chưa thể xóa", "Kết thúc và lưu phiên hiện tại trước khi xóa lịch sử.", parent=self.root)
            return
        if not self.store.records:
            return
        if not messagebox.askyesno("Xóa lịch sử?", "Xóa toàn bộ lịch sử và thống kê?\nỨng dụng sẽ lưu một bản sao JSON trước khi xóa.", parent=self.root):
            return
        try:
            self.store.write(f"history-backup-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json", self.store.records)
            self.store.write("focus_history.json", [])
            self.store.records = []
            self.refresh_stats()
            self.render()
        except OSError as exc:
            messagebox.showerror("Chưa xóa được", str(exc), parent=self.root)

    def import_history(self):
        """Nhập lịch sử phiên học từ tệp JSON cũ."""
        if self.session or self.unsaved:
            messagebox.showinfo("Nhập lịch sử", "Kết thúc và lưu phiên hiện tại trước khi nhập lịch sử.", parent=self.root)
            return
        path = filedialog.askopenfilename(parent=self.root, title="Chọn focus_history.json từ bản cũ", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(raw, list):
                raise ValueError("Tệp cần chứa một danh sách phiên học.")
            imported = [Store.clean_record(r, i) for i, r in enumerate(raw)]
            ids = {r["id"] for r in self.store.records}
            additions = [r for r in imported if r["id"] not in ids]
            merged = sorted(self.store.records + additions, key=lambda r: (r["date"], r.get("time", "")[-5:]), reverse=True)
            self.store.write("focus_history.json", merged)
            self.store.records = merged
            messagebox.showinfo("Đã nhập", f"Đã thêm {len(additions)} phiên. Tệp gốc được giữ nguyên.", parent=self.root)
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            messagebox.showerror("Không nhập được lịch sử", str(exc), parent=self.root)

    def close(self):
        """Xử lý sự kiện đóng cửa sổ ứng dụng."""
        if self.strict_overlay and not self.strict_overlay.closed:
            self.strict_overlay.confirm_end()
            return
        if self.page == "settings" and self.draft_signature() != self.draft_original:
            if not messagebox.askyesno("Cài đặt chưa lưu", "Thoát và bỏ thay đổi cài đặt?", parent=self.root):
                return
        if self.unsaved and not self.retry_save():
            messagebox.showwarning("Phiên chưa được lưu", "Ứng dụng sẽ giữ mở để tránh mất phiên. Hãy thử lưu lại hoặc xuất CSV để có bản sao.", parent=self.root)
            return
        if self.session:
            was_running = self.session.state == "running"
            if was_running:
                self.session.pause("Đã đóng ứng dụng; phiên có thể tiếp tục sau.")
            choice = messagebox.askyesnocancel("Thoát Focus Guard", "Có: kết thúc và lưu phiên.\nKhông: lưu tạm để tiếp tục ở lần mở sau.\nHủy: ở lại ứng dụng.", parent=self.root)
            if choice is None:
                if was_running:
                    self.session.resume()
                return
            if choice:
                self.finish(False)
                if self.unsaved:
                    return
            else:
                self.checkpoint()
                if self.storage_error:
                    return
        self.closing = True
        if self.job:
            self.root.after_cancel(self.job)
        self.close_popup(force=True)
        self.root.destroy()


# ==========================================
# PHẦN 11: ĐIỂM KHỞI CHẠY CHƯƠNG TRÌNH (MAIN)
# ==========================================
def main():
    if "--self-test" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="focusguard-selftest-") as directory:
            root = tk.Tk()
            root.withdraw()
            app = FocusGuard(root, directory)
            app.settings.update(sound=False, popup=False)
            for page in ("study", "settings", "stats"):
                app.show_page(page)
                root.update_idletasks()
            app.main_action()
            app.manual_pause()
            assert app.session.state == "paused"
            app.finish(False)
            assert len(Store(directory).records) == 1
            assert not app.storage_error
            app.closing = True
            root.destroy()
        return
    mutex = None
    if os.name == "nt":
        ctypes.windll.kernel32.CreateMutexW.restype = wintypes.HANDLE
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FocusGuardStudyV2")
        if mutex and ctypes.windll.kernel32.GetLastError() == 183:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Focus Guard", "Focus Guard đang mở. Hãy chuyển về cửa sổ ứng dụng hiện có.")
            root.destroy()
            return
    root = tk.Tk()
    FocusGuard(root)
    try:
        root.mainloop()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()