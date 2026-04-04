import customtkinter as ctk
import os
import shutil
import zipfile
import tempfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import subprocess
import json
import threading
import urllib.request
import urllib.error

try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False

try:
    import py7zr
    _HAS_7Z = True
except ImportError:
    _HAS_7Z = False

try:
    import rarfile
    _HAS_RAR = True
except ImportError:
    _HAS_RAR = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False


_SCRIPT_DIR = Path(__file__).resolve().parent


def _read_local_version():
    try:
        return (_SCRIPT_DIR / "version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


VERSION = _read_local_version().lstrip("vV").strip()

_GITHUB_RAW = "https://raw.githubusercontent.com/NixxGame/DBDPakLoader/main"
_GITHUB_VER_URL = _GITHUB_RAW + "/version.txt"

_UPDATE_FILES = ["loader.py", "requirements.txt", "version.txt"]


def _format_bytes(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        num /= 1024
        if num < 1024:
            return f"{num:.2f} {unit}"
    return f"{num:.2f} PB"


def _find_unrar_tool():
    for name in ("unrarw64.exe", "UnRAR.exe", "unrar.exe"):
        local = _SCRIPT_DIR / name
        if local.exists():
            return str(local)
    for name in ("unrar", "unrarw64", "UnRAR", "WinRAR", "Rar"):
        try:
            subprocess.run([name], capture_output=True)
            return name
        except FileNotFoundError:
            continue
    return None


def _extract_archive(archive_path: str, dest_dir: str, suffix: str):
    if suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)

    elif suffix == ".7z":
        if not _HAS_7Z:
            raise RuntimeError("7-Zip support requires py7zr.\n\nRun:  pip install py7zr")
        with py7zr.SevenZipFile(archive_path, mode="r") as sz:
            sz.extractall(path=dest_dir)

    elif suffix in (".rar", ".cbr"):
        unrar_tool = _find_unrar_tool()
        if _HAS_RAR and unrar_tool:
            try:
                rarfile.UNRAR_TOOL = unrar_tool
                with rarfile.RarFile(archive_path, "r") as rf:
                    rf.extractall(dest_dir)
                return
            except Exception:
                pass

        if unrar_tool:
            r = subprocess.run(
                [unrar_tool, "x", "-y", "-o+", archive_path, dest_dir + os.sep],
                capture_output=True
            )
            if r.returncode == 0:
                return

        raise RuntimeError("Could not extract RAR file.\n\nPlace UnRAR.exe in the same folder as loader.py")

    else:
        raise RuntimeError(f"Unsupported archive format: {suffix}")


def _find_paks_from_game_root(root: Path) -> Path | None:
    candidates = [
        root / "DeadByDaylight" / "Content" / "Paks",
        root / "DeadByDaylight" / "Content" / "Paks" / "paks",
        root / "Content" / "Paks",
        root / "Content" / "Paks" / "paks",
        root / "DeadByDaylight" / "Content" / "Paks" / "Paks",
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    for c in root.rglob("Paks"):
        if c.is_dir() and "deadbydaylight" in str(c).lower():
            return c
    return None


def _registry_get_value(key_path: str, value_name: str) -> str | None:
    if not _HAS_WINREG:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
        value, _ = winreg.QueryValueEx(key, value_name)
        return value
    except Exception:
        return None


def _auto_detect_dbd_paks_path() -> tuple[str | None, str]:
    common_roots = [
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Dead by Daylight"),
        Path(r"C:\Program Files\Steam\steamapps\common\Dead by Daylight"),
        Path(r"D:\SteamLibrary\steamapps\common\Dead by Daylight"),
        Path(r"E:\SteamLibrary\steamapps\common\Dead by Daylight"),
        Path(r"C:\Program Files\Epic Games\DeadByDaylight"),
        Path(r"D:\Program Files\Epic Games\DeadByDaylight"),
        Path(r"C:\XboxGames\Dead by Daylight"),
        Path(r"D:\XboxGames\Dead by Daylight"),
    ]

    for root in common_roots:
        if root.exists():
            paks = _find_paks_from_game_root(root)
            if paks:
                return (str(paks), f"Auto-detected from common paths: {root}")

    if _HAS_WINREG:
        steam_path = _registry_get_value(r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")
        if steam_path:
            guess = Path(steam_path) / "steamapps" / "common" / "Dead by Daylight"
            if guess.exists():
                paks = _find_paks_from_game_root(guess)
                if paks:
                    return (str(paks), "Auto-detected from Steam registry")

    return (None, "Could not auto-detect Dead by Daylight install folder")


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_ROOT = "#0d0d10"
BG_PANEL = "#13131a"
BG_CARD = "#1c1c25"
BG_CARD_HOV = "#22222e"
BG_FIELD = "#0f0f16"
ACCENT = "#c084fc"
ACCENT_DIM = "#7c3aed"
GREEN = "#4ade80"
RED = "#f87171"
ORANGE = "#fb923c"
TEXT_PRI = "#f0f0ff"
TEXT_SEC = "#7070a0"
TEXT_MUT = "#3a3a5c"

_MAX_NAME_CHARS = 22


def _truncate(name: str, limit: int = _MAX_NAME_CHARS) -> str:
    return name if len(name) <= limit else name[:limit - 1] + "…"


_FONT_LOGO = None
_FONT_LOGO_SUB = None
_FONT_LABEL_SM = None
_FONT_BODY = None
_FONT_BODY_MED = None
_FONT_BODY_BOLD = None
_FONT_TITLE = None
_FONT_BTN_LG = None
_FONT_MONO = None
_FONT_DOT = None
_FONT_PENCIL = None
_FONT_STATUS = None


def _init_fonts():
    global _FONT_LOGO, _FONT_LOGO_SUB, _FONT_LABEL_SM, _FONT_BODY, _FONT_BODY_MED
    global _FONT_BODY_BOLD, _FONT_TITLE, _FONT_BTN_LG, _FONT_MONO, _FONT_DOT
    global _FONT_PENCIL, _FONT_STATUS

    _FONT_LOGO = ctk.CTkFont(family="Segoe UI Black", size=24, weight="bold")
    _FONT_LOGO_SUB = ctk.CTkFont(family="Segoe UI", size=15, weight="bold")
    _FONT_LABEL_SM = ctk.CTkFont(family="Segoe UI", size=9, weight="bold")
    _FONT_BODY = ctk.CTkFont(family="Segoe UI", size=11)
    _FONT_BODY_MED = ctk.CTkFont(family="Segoe UI", size=12)
    _FONT_BODY_BOLD = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
    _FONT_TITLE = ctk.CTkFont(family="Segoe UI Black", size=24, weight="bold")
    _FONT_BTN_LG = ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
    _FONT_MONO = ctk.CTkFont(family="Consolas", size=12)
    _FONT_DOT = ctk.CTkFont(size=7)
    _FONT_PENCIL = ctk.CTkFont(size=11)
    _FONT_STATUS = ctk.CTkFont(family="Segoe UI", size=11)


class ModCard(ctk.CTkFrame):
    def __init__(self, master, folder_name: str, on_select, on_rename, **kw):
        super().__init__(master, fg_color=BG_CARD, corner_radius=6, height=30, **kw)
        self.pack_propagate(False)

        self._normal = BG_CARD
        self._hovered = BG_CARD_HOV
        self._selected_col = "#1e1830"
        self._is_selected = False

        self.folder_name = folder_name
        self.on_select = on_select
        self.on_rename = on_rename

        self._build()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._name_lbl.bind("<Button-1>", self._click)

        self._name_lbl.bind("<Enter>", self._on_enter)
        self._name_lbl.bind("<Leave>", self._on_leave)
        self._stripe.bind("<Enter>", self._on_enter)
        self._stripe.bind("<Leave>", self._on_leave)
        self._dot.bind("<Enter>", self._on_enter)
        self._dot.bind("<Leave>", self._on_leave)

    def _build(self):
        self._stripe = ctk.CTkFrame(self, width=3, corner_radius=2, fg_color=ACCENT_DIM)
        self._stripe.pack(side="left", fill="y", padx=(6, 0), pady=3)

        self._name_lbl = ctk.CTkLabel(
            self, text=_truncate(self.folder_name),
            font=_FONT_BODY, text_color=TEXT_PRI, anchor="w"
        )
        self._name_lbl.pack(side="left", fill="x", expand=True, padx=(8, 2), pady=4)

        self._dot = ctk.CTkLabel(self, text="●", width=14, font=_FONT_DOT, text_color=TEXT_MUT)
        self._dot.pack(side="right", padx=(0, 6))

        self._pencil_btn = ctk.CTkButton(
            self, text="✏", width=22, height=22, fg_color="transparent",
            hover_color="#2a2040", text_color=ACCENT, font=_FONT_PENCIL,
            corner_radius=4, command=self._rename
        )

    def _on_enter(self, _=None):
        if not self._is_selected:
            self.configure(fg_color=self._hovered)
        self._pencil_btn.pack(side="right", padx=(0, 2), before=self._dot)

    def _on_leave(self, _=None):
        if not self._is_selected:
            self.configure(fg_color=self._normal)
        try:
            x, y = self.winfo_pointerxy()
            wx, wy = self.winfo_rootx(), self.winfo_rooty()
            if not (wx <= x <= wx + self.winfo_width() and wy <= y <= wy + self.winfo_height()):
                self._pencil_btn.pack_forget()
        except Exception:
            self._pencil_btn.pack_forget()

    def _click(self, _=None):
        self.on_select(self.folder_name)

    def _rename(self):
        self.on_rename(self.folder_name)

    def update_name(self, new_name: str):
        self.folder_name = new_name
        self._name_lbl.configure(text=_truncate(new_name))

    def set_selected(self, selected: bool):
        self._is_selected = selected
        if selected:
            self.configure(fg_color=self._selected_col)
            self._stripe.configure(fg_color=ACCENT)
            self._name_lbl.configure(text_color=ACCENT)
        else:
            self.configure(fg_color=self._normal)
            self._stripe.configure(fg_color=ACCENT_DIM)
            self._name_lbl.configure(text_color=TEXT_PRI)

    def set_installed(self, installed: bool):
        self._dot.configure(text_color=GREEN if installed else TEXT_MUT)


class ShareDialog(ctk.CTkToplevel):
    def __init__(self, master, mod_name: str, mod_path: str):
        super().__init__(master)
        self.title("Share Mod")
        self.geometry("520x300")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_PANEL)

        self.mod_name = mod_name
        self.mod_path = mod_path
        self._link = ""

        self._build()
        self._start_upload()

    def _build(self):
        ctk.CTkLabel(
            self, text="📤  Share Mod",
            font=ctk.CTkFont(family="Segoe UI Black", size=18, weight="bold"),
            text_color=ACCENT
        ).pack(pady=(24, 4))

        self._mod_lbl = ctk.CTkLabel(
            self, text=self.mod_name,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_SEC
        )
        self._mod_lbl.pack()

        ctk.CTkFrame(self, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=24, pady=16)

        self._status_lbl = ctk.CTkLabel(
            self, text="⏳  Zipping mod…",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_PRI
        )
        self._status_lbl.pack(pady=(0, 10))

        self._progress = ctk.CTkProgressBar(
            self, width=460, height=8,
            fg_color=BG_FIELD, progress_color=ACCENT
        )
        self._progress.pack(pady=(0, 16))
        self._progress.set(0)
        self._progress.configure(mode="indeterminate")
        self._progress.start()

        self._link_frame = ctk.CTkFrame(self, fg_color="transparent")

        self._link_entry = ctk.CTkEntry(
            self._link_frame, width=340, height=36,
            fg_color=BG_FIELD, text_color=TEXT_PRI,
            border_color=ACCENT_DIM, corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=12),
            state="readonly"
        )
        self._link_entry.pack(side="left", padx=(0, 8))

        self._copy_btn = ctk.CTkButton(
            self._link_frame, text="📋 Copy", width=90, height=36,
            fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
            corner_radius=8, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._copy_link
        )
        self._copy_btn.pack(side="left")

        self._note_lbl = ctk.CTkLabel(
            self,
            text="Links never expire  •  powered by GoFile.io",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=TEXT_MUT
        )

        self._close_btn = ctk.CTkButton(
            self, text="Close", width=100, height=34,
            fg_color=BG_CARD, hover_color=BG_CARD_HOV, text_color=TEXT_PRI,
            corner_radius=8, command=self.destroy
        )
        self._close_btn.pack(side="bottom", pady=(0, 16))

    def _start_upload(self):
        threading.Thread(target=self._zip_and_upload, daemon=True).start()

    def _zip_and_upload(self):
        import http.client
        import ssl
        import time

        try:
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = os.path.join(tmp, f"{self.mod_name}.zip")

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                    for fname in os.listdir(self.mod_path):
                        fpath = os.path.join(self.mod_path, fname)
                        if os.path.isfile(fpath):
                            zf.write(fpath, fname)

                zip_size = os.path.getsize(zip_path)
                self.after(0, lambda: self._status_lbl.configure(text="⏳  Getting upload server…"))

                ctx = ssl.create_default_context()

                def https_get(host, path):
                    c = http.client.HTTPSConnection(host, timeout=15, context=ctx)
                    c.request("GET", path, headers={"User-Agent": "DBDPakLoader/1.0"})
                    r = c.getresponse()
                    data = json.loads(r.read().decode())
                    c.close()
                    return data

                info = https_get("api.gofile.io", "/servers")
                if info.get("status") != "ok":
                    raise RuntimeError("GoFile: could not get server list")
                server = info["data"]["servers"][0]["name"]

                self.after(0, lambda: self._status_lbl.configure(text=f"⬆️  Uploading {_format_bytes(zip_size)}…"))
                self.after(0, lambda: self._progress.configure(mode="determinate"))
                self.after(0, self._progress.stop)
                self.after(0, lambda: self._progress.set(0))

                boundary = b"DBDPakBoundary"
                zip_name = f"{self.mod_name}.zip".encode()

                header = (
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="file"; filename="' + zip_name + b'"\r\n'
                    b"Content-Type: application/zip\r\n\r\n"
                )

                footer = b"\r\n--" + boundary + b"--\r\n"
                total = len(header) + zip_size + len(footer)

                conn = http.client.HTTPSConnection(f"{server}.gofile.io", timeout=120, context=ctx)
                conn.putrequest("POST", "/contents/uploadfile")
                conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary.decode()}")
                conn.putheader("Content-Length", str(total))
                conn.putheader("User-Agent", "DBDPakLoader/1.0")
                conn.putheader("Connection", "close")
                conn.endheaders()

                conn.send(header)
                sent = len(header)
                chunk = 65536
                t_start = time.monotonic()
                t_last = t_start

                with open(zip_path, "rb") as fh:
                    while True:
                        data = fh.read(chunk)
                        if not data:
                            break
                        conn.send(data)
                        sent += len(data)

                        now = time.monotonic()
                        frac = min(sent / total, 1.0)

                        if now - t_last >= 0.1:
                            elapsed = max(now - t_start, 0.001)
                            speed = sent / elapsed
                            pct = int(frac * 100)
                            lbl_text = f"⬆️  {pct}%  •  {_format_bytes(int(speed))}/s"
                            self.after(0, lambda t=lbl_text: self._status_lbl.configure(text=t))
                            self.after(0, lambda f=frac: self._progress.set(f))
                            t_last = now

                conn.send(footer)
                self.after(0, lambda: self._status_lbl.configure(text="⏳  Waiting for server…"))
                self.after(0, lambda: self._progress.set(1.0))

                resp = conn.getresponse()
                result = json.loads(resp.read().decode())
                conn.close()

                if result.get("status") != "ok":
                    raise RuntimeError(f"GoFile error: {result.get('message', str(result))}")

                link = result["data"]["downloadPage"]
                self.after(0, lambda: self._on_success(link))

        except Exception as e:
            self.after(0, lambda: self._on_error(str(e)))

    def _on_success(self, link: str):
        self._link = link
        self._progress.stop()
        self._progress.configure(mode="determinate")
        self._progress.set(1.0)
        self._progress.configure(progress_color=GREEN)

        self._status_lbl.configure(text="✅  Upload complete! Share this link:", text_color=GREEN)

        self._link_entry.configure(state="normal")
        self._link_entry.insert(0, link)
        self._link_entry.configure(state="readonly")

        self._link_frame.pack(pady=(0, 8))
        self._note_lbl.pack(pady=(0, 4))

        self.clipboard_clear()
        self.clipboard_append(link)

    def _on_error(self, msg: str):
        self._progress.stop()
        self._progress.pack_forget()
        self._status_lbl.configure(
            text=f"❌  {msg}", text_color=RED,
            wraplength=460, justify="center"
        )

    def _copy_link(self):
        if self._link:
            self.clipboard_clear()
            self.clipboard_append(self._link)
            self._copy_btn.configure(text="✅ Copied!")
            self.after(2000, lambda: self._copy_btn.configure(text="📋 Copy"))


_BaseClass = TkinterDnD.Tk if _HAS_DND else ctk.CTk


class DBDModLoader(_BaseClass):
    def __init__(self):
        super().__init__()
        _init_fonts()

        self.title("DBD Pak Loader")
        self.geometry("1320x800")
        self.minsize(1100, 600)
        self.configure(bg=BG_ROOT)

        drive = os.path.splitdrive(os.getcwd())[0] + "\\"
        self.mods_dir = os.path.join(drive, "mods")

        self.config_file = _SCRIPT_DIR / "loader_config.json"
        self.custom_game_root = None
        self.custom_paks_path = None

        self.platforms = {
            "Steam  (-Windows)": {"suffix": "-Windows", "path": r"C:\Program Files (x86)\Steam\steamapps\common\Dead by Daylight\DeadByDaylight\Content\Paks"},
            "Epic Games  (-EGS)": {"suffix": "-EGS", "path": r"C:\Program Files\Epic Games\DeadByDaylight\DeadByDaylight\Content\Paks"},
            "Microsoft Store  (-WinGDK)": {"suffix": "-WinGDK", "path": r"C:\XboxGames\Dead by Daylight\Content\DeadByDaylight\Content\Paks"},
            "Custom Path": {"suffix": "", "path": ""}
        }

        self.current_mod = None
        self.current_mod_path = None
        self._card_map: dict[str, ModCard] = {}

        self._paks_cache: Path | None = None
        self._paks_cache_valid = False
        self._suffix_cache: str | None = None

        self._search_after_id = None

        os.makedirs(self.mods_dir, exist_ok=True)

        self._load_config()
        self._build_ui()
        self.load_mods()

        if _HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self._setup_drag_feedback()

        self.after(50, self._attempt_auto_detect)
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _load_config(self):
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                self.custom_game_root = data.get("custom_game_root")
                self.custom_paks_path = data.get("custom_paks_path")
                last_platform = data.get("last_platform")
                if last_platform and last_platform in self.platforms:
                    self._pending_platform = last_platform
                else:
                    self._pending_platform = None
            except Exception:
                self._pending_platform = None

    def _save_config(self):
        data = {
            "custom_game_root": self.custom_game_root,
            "custom_paks_path": self.custom_paks_path,
            "last_platform": self.platform_var.get()
        }
        try:
            self.config_file.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except Exception:
            pass

    def _attempt_auto_detect(self):
        detected_path, msg = _auto_detect_dbd_paks_path()
        if detected_path:
            self.custom_paks_path = detected_path
            self.platforms["Custom Path"]["path"] = detected_path
            self._invalidate_paks_cache()
            self._save_config()
            self.status_var.set(f"✅ {msg}")
        else:
            self.status_var.set(msg)

    def _invalidate_paks_cache(self):
        self._paks_cache_valid = False
        self._paks_cache = None
        self._suffix_cache = None

    def get_active_paks_path(self) -> Path | None:
        if self._paks_cache_valid:
            return self._paks_cache

        platform = self.platform_var.get()
        if platform == "Custom Path":
            p = Path(self.custom_paks_path) if self.custom_paks_path else None
        else:
            p = Path(self.platforms[platform]["path"])

        result = p if (p and p.exists()) else None
        self._paks_cache = result
        self._paks_cache_valid = True
        return result

    def get_active_suffix(self) -> str:
        if self._suffix_cache is not None:
            return self._suffix_cache
        platform = self.platform_var.get()
        s = "" if platform == "Custom Path" else self.platforms[platform]["suffix"]
        self._suffix_cache = s
        return s

    def _build_ui(self):
        self._build_statusbar()
        self._build_sidebar()
        self._build_main()

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=26, fg_color=BG_PANEL, corner_radius=0)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)

        self.status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(bar, textvariable=self.status_var, font=_FONT_STATUS, text_color=TEXT_SEC).pack(side="left", padx=16, pady=4)

        ctk.CTkLabel(bar, text=f"v{VERSION}", font=_FONT_STATUS, text_color=TEXT_MUT).pack(side="right", padx=16, pady=4)

        self._update_btn = ctk.CTkButton(
            bar, text="⬆ Update Available — Click to install",
            height=20, font=_FONT_STATUS,
            fg_color="#1a2e1a", hover_color="#223d22",
            border_width=1, border_color=GREEN,
            text_color=GREEN, corner_radius=6,
            command=self._do_update
        )

        self._dl_frame = ctk.CTkFrame(bar, fg_color="transparent")
        self._dl_label = ctk.CTkLabel(self._dl_frame, text="", font=_FONT_STATUS, text_color=TEXT_SEC)
        self._dl_label.pack(side="left", padx=(0, 10))

        self._dl_bar = ctk.CTkProgressBar(self._dl_frame, width=220, height=10, progress_color=ACCENT)
        self._dl_bar.pack(side="left")
        self._dl_bar.set(0)

        self._dl_frame.pack_forget()

    def _show_download_bar(self, text="Downloading..."):
        self._dl_label.configure(text=text)
        self._dl_bar.set(0)
        self._dl_frame.pack(side="right", padx=(0, 10), pady=3)

    def _hide_download_bar(self):
        self._dl_frame.pack_forget()

    def _set_download_progress(self, frac: float, text: str | None = None):
        if text is not None:
            self._dl_label.configure(text=text)
        self._dl_bar.set(max(0, min(frac, 1)))

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=290, fg_color=BG_PANEL, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        logo_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_row.pack(fill="x", padx=20, pady=(20, 0))
        ctk.CTkLabel(logo_row, text="DBD", font=_FONT_LOGO, text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(logo_row, text=" Pak Loader", font=_FONT_LOGO_SUB, text_color=TEXT_PRI).pack(side="left", pady=(1, 0))

        ctk.CTkFrame(sidebar, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(sidebar, text="MODS", font=_FONT_LABEL_SM, text_color=TEXT_MUT).pack(anchor="w", padx=20, pady=(10, 3))

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(
            sidebar, textvariable=self.search_var,
            placeholder_text="Search mods...", height=34,
            fg_color=BG_FIELD, text_color=TEXT_PRI,
            border_color=TEXT_MUT, corner_radius=9, font=_FONT_BODY_MED
        )
        self.search_entry.pack(fill="x", padx=16, pady=(0, 10))
        self.search_entry.bind("<KeyRelease>", self._on_search_key)

        self.mods_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent",
            scrollbar_button_color=TEXT_MUT, scrollbar_button_hover_color=TEXT_SEC
        )
        self.mods_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        ctk.CTkFrame(sidebar, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=16)

        btn_area = ctk.CTkFrame(sidebar, fg_color="transparent")
        btn_area.pack(fill="x", padx=12, pady=10)

        ctk.CTkButton(
            btn_area, text="＋  Import Mod", height=40,
            font=_FONT_BODY_BOLD, fg_color=ACCENT, hover_color=ACCENT_DIM,
            text_color="black", corner_radius=9, command=self.import_mod_folder
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            btn_area, text="↺  Refresh", height=32, font=_FONT_BODY_MED,
            fg_color=BG_CARD, hover_color=BG_CARD_HOV, text_color=TEXT_PRI,
            corner_radius=9, command=self.load_mods
        ).pack(fill="x")

    def _build_main(self):
        self.main_area = ctk.CTkFrame(self, fg_color=BG_ROOT)
        self.main_area.pack(side="left", fill="both", expand=True)

        topbar = ctk.CTkFrame(self.main_area, fg_color=BG_PANEL, height=58, corner_radius=0)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        plat_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        plat_frame.pack(side="left", padx=24, pady=8)
        ctk.CTkLabel(plat_frame, text="PLATFORM", font=_FONT_LABEL_SM, text_color=TEXT_MUT).pack(anchor="w")

        default_plat = getattr(self, "_pending_platform", None) or "Microsoft Store  (-WinGDK)"
        self.platform_var = ctk.StringVar(value=default_plat)

        self.platform_menu = ctk.CTkOptionMenu(
            plat_frame, values=list(self.platforms.keys()),
            variable=self.platform_var, command=self.on_platform_change,
            width=290, height=30,
            fg_color=BG_FIELD, button_color=ACCENT_DIM, button_hover_color=ACCENT,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOV,
            font=_FONT_BODY_MED, text_color=TEXT_PRI, corner_radius=7
        )
        self.platform_menu.pack(anchor="w")

        self.custom_path_btn = ctk.CTkButton(
            topbar, text="⚙ Set Custom Path", height=34, width=150,
            fg_color=BG_FIELD, hover_color=BG_CARD, border_width=1,
            border_color=TEXT_MUT, text_color=TEXT_PRI, corner_radius=9,
            command=self.set_custom_game_path
        )
        self.custom_path_btn.pack(side="right", padx=(0, 12), pady=12)

        btn_group = ctk.CTkFrame(topbar, fg_color="transparent")
        btn_group.pack(side="right", padx=20, pady=12)

        ctk.CTkButton(
            btn_group, text="🧹  Clean DBD", height=34, width=130,
            fg_color="#2e1010", hover_color="#4a1515", border_width=1,
            border_color=RED, text_color=RED, corner_radius=9, command=self.clean_paks_folder
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_group, text="📂  Open Paks Folder", height=34, width=180,
            fg_color=BG_FIELD, hover_color=BG_CARD, border_width=1,
            border_color=TEXT_MUT, text_color=TEXT_PRI, corner_radius=9,
            command=self.open_paks_folder
        ).pack(side="left")

        self._content = ctk.CTkFrame(self.main_area, fg_color="transparent")
        self._content.pack(fill="both", expand=True)

        self.empty_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(self.empty_frame, text="📦", font=ctk.CTkFont(size=60)).pack()
        ctk.CTkLabel(self.empty_frame, text="Select a mod to get started", font=_FONT_TITLE, text_color=TEXT_PRI).pack(pady=(10, 5))

        dnd_hint = ("Drag & drop archives or folders here to import" if _HAS_DND else "Import a mod folder or archive using the button below.")
        ctk.CTkLabel(self.empty_frame, text=dnd_hint, font=_FONT_BODY_MED, text_color=TEXT_SEC, wraplength=380).pack()

        self.detail_frame = ctk.CTkFrame(self._content, fg_color="transparent")

        title_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        title_row.pack(fill="x", padx=30, pady=(24, 0))

        self.mod_title = ctk.CTkLabel(title_row, text="", font=_FONT_TITLE, text_color=TEXT_PRI, anchor="w")
        self.mod_title.pack(side="left")

        self.mod_status_badge = ctk.CTkLabel(title_row, text="", font=_FONT_BODY, fg_color=BG_CARD, corner_radius=7, padx=10, pady=3)
        self.mod_status_badge.pack(side="left", padx=(14, 0), pady=(3, 0))

        self.mod_stats_badge = ctk.CTkLabel(title_row, text="", font=_FONT_BODY, fg_color=BG_CARD, corner_radius=7, padx=10, pady=3, text_color=TEXT_SEC)
        self.mod_stats_badge.pack(side="left", padx=(10, 0), pady=(3, 0))

        self.conflict_label = ctk.CTkLabel(title_row, text="", font=_FONT_BODY, text_color=ORANGE)
        self.conflict_label.pack(side="left", padx=(10, 0), pady=(3, 0))

        ctk.CTkButton(
            title_row, text="📁 Open Folder", width=110, height=28,
            fg_color="transparent", hover_color=BG_CARD_HOV,
            border_width=1, border_color=TEXT_MUT, text_color=TEXT_PRI,
            corner_radius=7, command=self._open_current_mod_folder
        ).pack(side="left", padx=(16, 0), pady=(3, 0))

        ctk.CTkButton(
            title_row, text="✏ Rename", width=90, height=28,
            fg_color="transparent", hover_color="#2a2040",
            border_width=1, border_color=ACCENT_DIM, text_color=ACCENT,
            corner_radius=7, command=self._rename_current
        ).pack(side="left", padx=(8, 0), pady=(3, 0))

        ctk.CTkButton(
            title_row, text="🗑 Delete", width=80, height=28,
            fg_color="transparent", hover_color="#3a1a1a",
            border_width=1, border_color=RED, text_color=RED,
            corner_radius=7, command=self._delete_current
        ).pack(side="left", padx=(8, 0), pady=(3, 0))

        ctk.CTkButton(
            title_row, text="🔗 Share", width=80, height=28,
            fg_color="transparent", hover_color="#1a2a1a",
            border_width=1, border_color=GREEN, text_color=GREEN,
            corner_radius=7, command=self._share_current
        ).pack(side="left", padx=(8, 0), pady=(3, 0))

        ctk.CTkLabel(self.detail_frame, text="Files in mod", font=_FONT_LABEL_SM, text_color=TEXT_MUT).pack(anchor="w", padx=30, pady=(18, 5))

        files_outer = ctk.CTkFrame(self.detail_frame, fg_color=BG_FIELD, corner_radius=12)
        files_outer.pack(fill="both", expand=True, padx=30)

        self.files_container = ctk.CTkScrollableFrame(files_outer, fg_color="transparent", scrollbar_button_color=TEXT_MUT)
        self.files_container.pack(fill="both", expand=True, padx=4, pady=4)

        btn_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=30, pady=20)

        self.add_btn = ctk.CTkButton(
            btn_row, text="ADD TO PAKS", height=50, font=_FONT_BTN_LG,
            fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
            corner_radius=12, command=self.install_mod
        )
        self.add_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.remove_btn = ctk.CTkButton(
            btn_row, text="REMOVE FROM PAKS", height=50, font=_FONT_BTN_LG,
            fg_color="#2e1010", hover_color="#4a1515", border_width=1,
            border_color=RED, text_color=RED, corner_radius=12,
            command=self.uninstall_mod, state="disabled"
        )
        self.remove_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _setup_drag_feedback(self):
        self.original_bg = self.main_area.cget("fg_color")
        self.main_area.bind("<Enter>", lambda e: self.main_area.configure(fg_color="#1a1a2e") if _HAS_DND else None)
        self.main_area.bind("<Leave>", lambda e: self.main_area.configure(fg_color=self.original_bg) if _HAS_DND else None)

    def _on_search_key(self, _=None):
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(180, self.load_mods)

    def load_mods(self):
        for w in self.mods_scroll.winfo_children():
            w.destroy()
        self._card_map.clear()

        query = self.search_var.get().strip().lower()
        folders = [f for f in os.listdir(self.mods_dir) if os.path.isdir(os.path.join(self.mods_dir, f))]
        if query:
            folders = [f for f in folders if query in f.lower()]

        if not folders:
            ctk.CTkLabel(self.mods_scroll, text="No mods found.", font=_FONT_BODY_MED, text_color=TEXT_SEC, justify="center").pack(pady=40)
            return

        paks_path = self.get_active_paks_path()
        suffix = self.get_active_suffix()
        installed_set = self._batch_check_installed(folders, paks_path, suffix)

        folders.sort(key=lambda f: (0 if f in installed_set else 1, f.lower()))

        for folder in folders:
            card = ModCard(self.mods_scroll, folder, on_select=self.select_mod, on_rename=self.rename_mod)
            card.pack(fill="x", pady=2, padx=2)
            self._card_map[folder] = card
            card.set_installed(folder in installed_set)

        if self.current_mod and self.current_mod in self._card_map:
            self._card_map[self.current_mod].set_selected(True)

    def _batch_check_installed(self, folders: list[str], paks_path: Path | None, suffix: str) -> set[str]:
        installed = set()
        if not paks_path or not paks_path.exists():
            return installed
        try:
            paks_files = {f.lower() for f in os.listdir(paks_path)}
        except OSError:
            return installed

        for folder in folders:
            mod_path = os.path.join(self.mods_dir, folder)
            try:
                for file in os.listdir(mod_path):
                    if os.path.isfile(os.path.join(mod_path, file)):
                        base, ext = os.path.splitext(file)
                        new_name = (
                            "-".join(base.split("-")[:-1]) + suffix + ext
                            if "-" in base and suffix else
                            base + suffix + ext
                        )
                        if new_name.lower() in paks_files:
                            installed.add(folder)
                            break
            except OSError:
                continue

        return installed

    def _on_drop(self, event):
        self.main_area.configure(fg_color=self.original_bg)
        raw = event.data.strip()
        paths = []

        if raw.startswith("{"):
            import re
            paths = re.findall(r'\{([^}]+)\}|(\S+)', raw)
            paths = [a or b for a, b in paths]
        else:
            paths = raw.split()

        imported = 0
        for path_str in paths:
            path_str = path_str.strip().strip("{}")
            if path_str:
                try:
                    self._do_import(path_str, silent=len(paths) > 1)
                    imported += 1
                except Exception as e:
                    messagebox.showerror("Import Error", str(e))

        if len(paths) > 1:
            self.load_mods()
            self.status_var.set(f"Imported {imported} of {len(paths)} items")

    def select_mod(self, folder_name: str):
        for name, card in self._card_map.items():
            card.set_selected(name == folder_name)

        self.current_mod = folder_name
        self.current_mod_path = os.path.join(self.mods_dir, folder_name)

        self.empty_frame.place_forget()
        self.detail_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.mod_title.configure(text=folder_name)

        installed = self.is_mod_installed()
        if installed:
            self.mod_status_badge.configure(text="● Installed", text_color=GREEN, fg_color="#0d2a1a")
            self.add_btn.configure(text="RE-ADD TO PAKS", fg_color=ORANGE, hover_color="#c2410c", text_color="black")
            self.remove_btn.configure(state="normal")
        else:
            self.mod_status_badge.configure(text="● Not Installed", text_color=ORANGE, fg_color="#2a1a0d")
            self.add_btn.configure(text="ADD TO PAKS", fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black")
            self.remove_btn.configure(state="disabled")

        file_count, total_size = self._compute_mod_stats()
        self.mod_stats_badge.configure(text=f"{file_count} file(s) • {_format_bytes(total_size)}")

        conflicts = self._check_conflicts()
        self.conflict_label.configure(
            text=(f"⚠ Conflicts with: {', '.join(conflicts[:3])}" + ("..." if len(conflicts) > 3 else ""))
            if conflicts else ""
        )

        if folder_name in self._card_map:
            self._card_map[folder_name].set_installed(installed)

        self.show_final_files()

    def _compute_mod_stats(self) -> tuple[int, int]:
        if not self.current_mod_path:
            return (0, 0)
        total_files = total_size = 0
        for file in os.listdir(self.current_mod_path):
            p = os.path.join(self.current_mod_path, file)
            if os.path.isfile(p):
                total_files += 1
                try:
                    total_size += os.path.getsize(p)
                except Exception:
                    pass
        return (total_files, total_size)

    def _check_conflicts(self) -> list[str]:
        if not self.current_mod_path:
            return []

        suffix = self.get_active_suffix()
        target_files: set[str] = set()

        for file in os.listdir(self.current_mod_path):
            if os.path.isfile(os.path.join(self.current_mod_path, file)):
                base, ext = os.path.splitext(file)
                new_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)
                target_files.add(new_name.lower())

        conflicts = []
        for mod_folder in os.listdir(self.mods_dir):
            if mod_folder == self.current_mod:
                continue
            other_path = os.path.join(self.mods_dir, mod_folder)
            if not os.path.isdir(other_path):
                continue
            for file in os.listdir(other_path):
                if os.path.isfile(os.path.join(other_path, file)):
                    base, ext = os.path.splitext(file)
                    other_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)
                    if other_name.lower() in target_files:
                        conflicts.append(mod_folder)
                        break
        return conflicts

    def is_mod_installed(self) -> bool:
        if not self.current_mod:
            return False
        paks_path = self.get_active_paks_path()
        if not paks_path:
            return False
        result = self._batch_check_installed([self.current_mod], paks_path, self.get_active_suffix())
        return self.current_mod in result

    def on_platform_change(self, _):
        self._invalidate_paks_cache()
        self._save_config()

        paks_path = self.get_active_paks_path()
        suffix = self.get_active_suffix()
        installed_set = self._batch_check_installed(list(self._card_map.keys()), paks_path, suffix)

        for folder, card in self._card_map.items():
            card.set_installed(folder in installed_set)

        if self.current_mod:
            self.select_mod(self.current_mod)

    def _open_current_mod_folder(self):
        if self.current_mod_path and os.path.exists(self.current_mod_path):
            subprocess.Popen(f'explorer "{self.current_mod_path}"')
        else:
            messagebox.showerror("Error", "Mod folder not found.")

    def _rename_current(self):
        if self.current_mod:
            self.rename_mod(self.current_mod)

    def _delete_current(self):
        if self.current_mod:
            self.delete_mod(self.current_mod)

    def _share_current(self):
        if not self.current_mod or not self.current_mod_path:
            return
        ShareDialog(self, self.current_mod, self.current_mod_path)

    def rename_mod(self, folder_name: str):
        new_name = simpledialog.askstring("Rename Mod", f"New name for '{folder_name}':", initialvalue=folder_name, parent=self)
        if not new_name or new_name.strip() == folder_name:
            return

        new_name = new_name.strip()
        if any(c in new_name for c in r'\/:*?"<>|'):
            messagebox.showerror("Invalid Name", "Name contains invalid characters.")
            return

        old_path = os.path.join(self.mods_dir, folder_name)
        new_path = os.path.join(self.mods_dir, new_name)

        if os.path.exists(new_path):
            messagebox.showerror("Name Taken", f"A mod named '{new_name}' already exists.")
            return

        try:
            os.rename(old_path, new_path)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        if folder_name in self._card_map:
            card = self._card_map.pop(folder_name)
            card.update_name(new_name)
            self._card_map[new_name] = card

        if self.current_mod == folder_name:
            self.current_mod = new_name
            self.current_mod_path = new_path
            self.mod_title.configure(text=new_name)

        self.status_var.set(f"Renamed: {folder_name} → {new_name}")
        self.load_mods()

    def delete_mod(self, folder_name: str):
        if not messagebox.askyesno("Delete Mod", f"Remove '{folder_name}' from the loader?\n\nFiles in Paks folder will NOT be removed."):
            return

        try:
            shutil.rmtree(os.path.join(self.mods_dir, folder_name))
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        if self.current_mod == folder_name:
            self.current_mod = None
            self.current_mod_path = None
            self.detail_frame.place_forget()
            self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")

        self.load_mods()
        self.status_var.set(f"Deleted: {folder_name}")

    def show_final_files(self):
        for w in self.files_container.winfo_children():
            w.destroy()

        files = [f for f in os.listdir(self.current_mod_path) if os.path.isfile(os.path.join(self.current_mod_path, f))]
        suffix = self.get_active_suffix()

        if not files:
            ctk.CTkLabel(self.files_container, text="No files found in this mod folder.", font=_FONT_BODY_MED, text_color=TEXT_SEC).pack(padx=20, pady=16)
            return

        for i, file in enumerate(files):
            base, ext = os.path.splitext(file)
            new_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)

            row = ctk.CTkFrame(self.files_container, fg_color="#16162a" if i % 2 == 0 else "transparent", corner_radius=7)
            row.pack(fill="x", padx=4, pady=2)

            ctk.CTkLabel(row, text="→", font=_FONT_MONO, text_color=ACCENT, width=22).pack(side="left", padx=(10, 5), pady=7)
            ctk.CTkLabel(row, text=new_name, font=_FONT_MONO, text_color="#b8b8ff", anchor="w").pack(side="left", fill="x", expand=True, pady=7)

    def set_custom_game_path(self):
        folder = filedialog.askdirectory(title="Select Dead by Daylight Install Folder")
        if not folder:
            return

        root = Path(folder)
        paks = _find_paks_from_game_root(root)

        if not paks:
            messagebox.showerror("Invalid Folder", "Could not locate Content\\Paks inside that folder.")
            return

        self.custom_game_root = str(root)
        self.custom_paks_path = str(paks)
        self.platforms["Custom Path"]["path"] = str(paks)
        self.platform_var.set("Custom Path")

        self._invalidate_paks_cache()
        self._save_config()

        self.status_var.set(f"Custom path set: {paks}")
        self.load_mods()

        if self.current_mod:
            self.select_mod(self.current_mod)

    def clean_paks_folder(self):
        paks_path = self.get_active_paks_path()
        if not paks_path:
            messagebox.showerror("Not Found", "Paks folder not found.")
            return

        if not messagebox.askyesno("Clean DBD Paks", "This will remove ALL mod files from your Paks folder.\nContinue?"):
            return

        suffix = self.get_active_suffix()
        removed = 0

        for mod_folder in os.listdir(self.mods_dir):
            mod_path = Path(self.mods_dir) / mod_folder
            if not mod_path.is_dir():
                continue

            for file in os.listdir(mod_path):
                if os.path.isfile(mod_path / file):
                    base, ext = os.path.splitext(file)
                    final_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)
                    target = paks_path / final_name
                    if target.exists():
                        try:
                            target.unlink()
                            removed += 1
                        except Exception:
                            pass

        self._invalidate_paks_cache()
        self.status_var.set(f"🧹 Cleaned: removed {removed} file(s)")
        self.load_mods()

        if self.current_mod:
            self.select_mod(self.current_mod)

    def import_mod_folder(self):
        all_exts = "*.zip *.ZIP"
        if _HAS_RAR:
            all_exts += " *.rar *.RAR *.cbr"
        if _HAS_7Z:
            all_exts += " *.7z *.7Z"

        types = [("Mod archives", all_exts), ("ZIP archive", "*.zip *.ZIP")]
        if _HAS_RAR:
            types.append(("RAR archive", "*.rar *.RAR *.cbr"))
        if _HAS_7Z:
            types.append(("7-Zip archive", "*.7z *.7Z"))
        types.append(("All files", "*.*"))

        paths = filedialog.askopenfilenames(title="Select Mod Archive(s)", filetypes=types)
        if not paths:
            folder = filedialog.askdirectory(title="Or select a Mod Folder")
            if folder:
                paths = (folder,)

        if not paths:
            return

        for path in paths:
            try:
                self._do_import(path, silent=len(paths) > 1)
            except Exception as e:
                messagebox.showerror("Import Error", f"{Path(path).name}:\n{e}")

        self.load_mods()

    def _do_import(self, path: str, silent: bool = False):
        p = Path(path)

        if p.is_dir():
            name = p.name.strip()
            dest = os.path.join(self.mods_dir, name)

            if os.path.exists(dest):
                if not messagebox.askyesno("Overwrite", f"Mod '{name}' already exists.\nOverwrite?"):
                    return
                shutil.rmtree(dest)

            shutil.copytree(str(p), dest)

            if not silent:
                messagebox.showinfo("Imported", f"Mod '{name}' imported successfully.")

            self.status_var.set(f"Imported: {name}")
            if not silent:
                self.load_mods()
            return

        suffix = p.suffix.lower()
        name = p.stem

        if suffix not in {".zip", ".rar", ".cbr", ".7z"}:
            raise RuntimeError(f"Unsupported format: {suffix}")

        dest = os.path.join(self.mods_dir, name)

        if os.path.exists(dest):
            if not messagebox.askyesno("Overwrite", f"Mod '{name}' already exists.\nOverwrite?"):
                return
            shutil.rmtree(dest)

        self.status_var.set(f"Extracting {p.name}...")
        self.update_idletasks()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                _extract_archive(path, tmp, suffix)

                entries = [e for e in os.listdir(tmp) if not e.startswith("__MACOSX")]
                extracted_root = (
                    os.path.join(tmp, entries[0])
                    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0]))
                    else tmp
                )

                shutil.copytree(extracted_root, dest, dirs_exist_ok=True)

            if not silent:
                messagebox.showinfo("Imported", f"Mod '{name}' imported successfully.")

            self.status_var.set(f"Imported: {name}")
            if not silent:
                self.load_mods()

        except Exception:
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            raise

    def open_paks_folder(self):
        paks_path = self.get_active_paks_path()
        if not paks_path:
            messagebox.showerror("Not Found", "Paks folder not found.")
            return
        try:
            subprocess.Popen(f'explorer "{paks_path}"')
            self.status_var.set(f"Opened: {paks_path}")
        except Exception:
            messagebox.showerror("Error", "Could not open folder.")

    def install_mod(self):
        if not self.current_mod:
            return

        paks_path = self.get_active_paks_path()
        if not paks_path:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return

        suffix = self.get_active_suffix()
        conflicts = self._check_conflicts()

        if conflicts and not messagebox.askyesno("Conflict Detected", f"This mod conflicts with:\n{', '.join(conflicts)}\n\nContinue anyway?"):
            return

        self.add_btn.configure(text="ADDING…", state="disabled")
        self.status_var.set(f"Adding {self.current_mod}…")
        self.update_idletasks()

        try:
            files = [f for f in os.listdir(self.current_mod_path) if os.path.isfile(os.path.join(self.current_mod_path, f))]
            for file in files:
                src = Path(self.current_mod_path) / file
                base, ext = os.path.splitext(file)
                new_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)
                shutil.copy2(src, paks_path / new_name)

            self._invalidate_paks_cache()
            self.status_var.set(f"✅ {self.current_mod} added")
            messagebox.showinfo("Success", f"'{self.current_mod}' added successfully.")

            self.load_mods()
            self.select_mod(self.current_mod)

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            self.add_btn.configure(state="normal")

    def uninstall_mod(self):
        if not self.current_mod:
            return

        if not messagebox.askyesno("Confirm Remove", f"Remove '{self.current_mod}' from Paks folder?"):
            return

        paks_path = self.get_active_paks_path()
        if not paks_path:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return

        suffix = self.get_active_suffix()
        self.remove_btn.configure(state="disabled")
        self.status_var.set(f"Removing {self.current_mod}…")
        self.update_idletasks()

        try:
            files = [f for f in os.listdir(self.current_mod_path) if os.path.isfile(os.path.join(self.current_mod_path, f))]
            removed = 0

            for file in files:
                base, ext = os.path.splitext(file)
                new_name = ("-".join(base.split("-")[:-1]) + suffix + ext if "-" in base and suffix else base + suffix + ext)
                t = paks_path / new_name
                if t.exists():
                    t.unlink()
                    removed += 1

            self._invalidate_paks_cache()
            self.status_var.set(f"✅ {self.current_mod} removed")
            messagebox.showinfo("Removed", f"Removed {removed} file(s) for '{self.current_mod}'")

            self.load_mods()
            self.select_mod(self.current_mod)

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            self.remove_btn.configure(state="normal")

    def _check_for_update(self):
        try:
            req = urllib.request.Request(
                _GITHUB_VER_URL,
                headers={"User-Agent": "DBDPakLoader/1.0"}
            )

            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status != 200:
                    return
                latest = r.read().decode("utf-8", errors="ignore").strip()

            latest = latest.lstrip("vV").strip()
            local = VERSION.lstrip("vV").strip()

            if not latest:
                return

            if latest != local:
                self._latest_version = latest
                self.after(0, self._show_update_badge)

        except Exception:
            pass

    def _show_update_badge(self):
        latest = getattr(self, "_latest_version", "?")
        self._update_btn.configure(text=f"⬆  v{latest} available — click to update")
        self._update_btn.pack(side="right", padx=(0, 10), pady=3)

    def _do_update(self):
        latest = getattr(self, "_latest_version", "?")

        if not messagebox.askyesno(
            "Update DBD Pak Loader",
            f"Update from v{VERSION} → v{latest}?\n\nThe app will restart automatically after updating."
        ):
            return

        self._update_btn.configure(text="⬇  Downloading…", state="disabled")
        self._show_download_bar("Starting...")

        def _run():
            try:
                total_files = len(_UPDATE_FILES)

                for i, filename in enumerate(_UPDATE_FILES):
                    url = f"{_GITHUB_RAW}/{filename}"

                    req = urllib.request.Request(url, headers={"User-Agent": "DBDPakLoader/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        if r.status != 200:
                            raise RuntimeError(f"Failed downloading {filename} (HTTP {r.status})")

                        size = r.headers.get("Content-Length")
                        size = int(size) if size and size.isdigit() else None

                        dest = _SCRIPT_DIR / filename
                        tmp = dest.with_suffix(dest.suffix + ".new")

                        downloaded = 0
                        chunk_size = 65536

                        with open(tmp, "wb") as f:
                            while True:
                                chunk = r.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)

                                if size:
                                    file_frac = downloaded / size
                                else:
                                    file_frac = 0

                                overall_frac = (i + file_frac) / total_files
                                label = f"{filename} ({i+1}/{total_files})"

                                self.after(0, lambda frac=overall_frac, t=label: self._set_download_progress(frac, t))

                        tmp.replace(dest)

                self.after(0, self._hide_download_bar)
                self.after(0, self._restart_after_update)

            except Exception as e:
                self.after(0, self._hide_download_bar)
                self.after(0, lambda: (
                    messagebox.showerror("Update Failed", str(e)),
                    self._update_btn.configure(text="⬆  Update available — click to install", state="normal")
                ))

        threading.Thread(target=_run, daemon=True).start()

    def _restart_after_update(self):
        messagebox.showinfo("Update Complete", "Files updated successfully!\nThe app will now restart.")

        bat = _SCRIPT_DIR / "run.bat"
        if bat.exists():
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(bat)],
                cwd=str(_SCRIPT_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                ["python", str(_SCRIPT_DIR / "loader.py")],
                cwd=str(_SCRIPT_DIR)
            )

        self.destroy()


if __name__ == "__main__":
    app = DBDModLoader()
    app.mainloop()
