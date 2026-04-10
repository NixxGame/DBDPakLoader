import customtkinter as ctk
import os, shutil, zipfile, tempfile, http.client, ssl, io, time
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, Menu
import subprocess, json, threading

try:
    import winreg; _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False
try:
    import py7zr; _HAS_7Z = True
except ImportError:
    _HAS_7Z = False
try:
    import rarfile; _HAS_RAR = True
except ImportError:
    _HAS_RAR = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD; _HAS_DND = True
except ImportError:
    _HAS_DND = False
try:
    from PIL import Image; _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

_SCRIPT_DIR = Path(__file__).resolve().parent

def _read_local_version():
    try:
        return (_SCRIPT_DIR / "version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"

VERSION = _read_local_version().lstrip("vV").strip()

# ── Hardware ID ───────────────────────────────────────────────────────────────
def _get_hwid():
    import hashlib
    if _HAS_WINREG:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Cryptography")
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return hashlib.sha256(guid.encode()).hexdigest()[:32]
        except Exception:
            pass
    hwid_file = Path(r"C:\pakConfig") / "hwid.txt"
    try:
        hwid_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if hwid_file.exists():
        return hwid_file.read_text().strip()
    import uuid
    new_id = uuid.uuid4().hex
    try:
        hwid_file.write_text(new_id)
    except Exception:
        pass
    return new_id

_HWID = _get_hwid()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_bytes(n: int) -> str:
    if n < 1024: return f"{n} B"
    for u in ["KB","MB","GB","TB"]:
        n /= 1024
        if n < 1024: return f"{n:.2f} {u}"
    return f"{n:.2f} PB"

def _https_get_bytes(url, max_redirects=8):
    ctx = ssl.create_default_context()
    for _ in range(max_redirects):
        s = url.replace("https://","").replace("http://","")
        host = s.split("/")[0]
        path = "/" + "/".join(s.split("/")[1:])
        conn = (http.client.HTTPSConnection(host,timeout=60,context=ctx) 
                if url.startswith("https") else http.client.HTTPConnection(host,timeout=60))
        conn.request("GET", path, headers={"User-Agent":"DBDPakLoader/1.0"})
        resp = conn.getresponse()
        if resp.status in (301,302,303,307,308):
            url = resp.getheader("Location",""); conn.close()
            if not url: raise RuntimeError("Redirect with no Location")
            continue
        if resp.status != 200: raise RuntimeError(f"HTTP {resp.status}")
        data = resp.read(); conn.close(); return data
    raise RuntimeError("Too many redirects")

def _gofile_get_token():
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection("api.gofile.io", timeout=15, context=ctx)
    conn.request("POST", "/accounts", headers={"User-Agent": "DBDPakLoader/1.0", "Content-Length": "0"})
    data = json.loads(conn.getresponse().read().decode())
    conn.close()
    if data.get("status") != "ok":
        raise RuntimeError(f"GoFile: could not get token — {data.get('message', data)}")
    return data["data"]["token"]

def _gofile_get_content(content_id, token):
    data = _https_get_json(
        "api.gofile.io",
        f"/contents/{content_id}?wt=4fd6sg89d7s6&cache=true",
        token=token,
    )
    if data.get("status") != "ok":
        raise RuntimeError(f"GoFile API error: {data.get('message', 'unknown')}")
    return data["data"]

def _gofile_collect_files(content, token):
    results = []
    node_type = content.get("type", "")
    if node_type == "file":
        results.append((content["name"], content["link"]))
    elif node_type == "folder":
        for child in content.get("children", {}).values():
            if child.get("type") == "file":
                results.append((child["name"], child["link"]))
            elif child.get("type") == "folder":
                sub = _gofile_get_content(child["id"], token)
                results.extend(_gofile_collect_files(sub, token))
    return results

def _download_gofile_to_mod(page_url, mod_name, mods_dir, progress_cb=None):
    content_id = page_url.rstrip("/").split("/")[-1]
    token = _gofile_get_token()
    content = _gofile_get_content(content_id, token)
    files = _gofile_collect_files(content, token)

    if not files:
        raise RuntimeError(
            f"No files found in GoFile link: {page_url}\n"
            "Make sure the link is correct and the content hasn't been deleted."
        )

    if len(files) == 1 and files[0][0].lower().endswith(".zip"):
        fname, link = files[0]
        if progress_cb:
            progress_cb(0, 1, fname)
        data = _https_get_bytes(link)
        _import_mod_from_zip_bytes(data, mod_name, mods_dir)
        if progress_cb:
            progress_cb(1, 1, "Done")
        return

    dest = os.path.join(mods_dir, mod_name)
    os.makedirs(dest, exist_ok=True)
    for i, (fname, link) in enumerate(files):
        if progress_cb:
            progress_cb(i, len(files), fname)
        data = _https_get_bytes(link)
        with open(os.path.join(dest, fname), "wb") as fh:
            fh.write(data)
    if progress_cb:
        progress_cb(len(files), len(files), "Done")

def _https_get_json(host, path, token=""):
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, timeout=15, context=ctx)
    hdrs = {"User-Agent": "DBDPakLoader/1.0"}
    if token: hdrs["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=hdrs)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data

_WORKER_HOST = "pakmods.lizzy032408.workers.dev"

def _worker_post(path, payload):
    body = json.dumps(payload).encode()
    ctx  = ssl.create_default_context()
    conn = http.client.HTTPSConnection(_WORKER_HOST, timeout=10, context=ctx)
    conn.request("POST", path, body=body, headers={
        "User-Agent":   "DBDPakLoader/1.0",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data

def _import_mod_from_zip_bytes(data, mod_name, mods_dir):
    dest = os.path.join(mods_dir, mod_name)
    if os.path.exists(dest): shutil.rmtree(dest)
    with tempfile.TemporaryDirectory() as tmp:
        zp = os.path.join(tmp, f"{mod_name}.zip")
        with open(zp,"wb") as fh: fh.write(data)
        with zipfile.ZipFile(zp,"r") as zf: zf.extractall(tmp)
        entries = [e for e in os.listdir(tmp) if e != f"{mod_name}.zip" and not e.startswith("__MACOSX")]
        extracted = (os.path.join(tmp,entries[0]) if len(entries)==1 and os.path.isdir(os.path.join(tmp,entries[0])) else tmp)
        bad = os.path.join(extracted,f"{mod_name}.zip")
        if os.path.exists(bad): os.remove(bad)
        shutil.copytree(extracted,dest,dirs_exist_ok=True)

def _find_unrar_tool():
    for n in ("unrarw64.exe","UnRAR.exe","unrar.exe"):
        if (_SCRIPT_DIR/n).exists(): return str(_SCRIPT_DIR/n)
    for n in ("unrar","unrarw64","UnRAR","WinRAR","Rar"):
        try: subprocess.run([n],capture_output=True); return n
        except FileNotFoundError: pass
    return None

def _extract_archive(archive_path, dest_dir, suffix):
    if suffix == ".zip":
        with zipfile.ZipFile(archive_path,"r") as zf: zf.extractall(dest_dir)
    elif suffix == ".7z":
        if not _HAS_7Z: raise RuntimeError("py7zr required: pip install py7zr")
        with py7zr.SevenZipFile(archive_path,mode="r") as sz: sz.extractall(path=dest_dir)
    elif suffix in (".rar",".cbr"):
        ut = _find_unrar_tool()
        if _HAS_RAR and ut:
            try:
                rarfile.UNRAR_TOOL = ut
                with rarfile.RarFile(archive_path,"r") as rf: rf.extractall(dest_dir); return
            except Exception: pass
        if ut:
            r = subprocess.run([ut,"x","-y","-o+",archive_path,dest_dir+os.sep],capture_output=True)
            if r.returncode == 0: return
        raise RuntimeError("Could not extract RAR. Place UnRAR.exe next to loader.py")
    else:
        raise RuntimeError(f"Unsupported archive format: {suffix}")

def _find_paks_from_game_root(root):
    for c in [root/"DeadByDaylight"/"Content"/"Paks",
              root/"DeadByDaylight"/"Content"/"Paks"/"paks",
              root/"Content"/"Paks",
              root/"Content"/"Paks"/"paks",
              root/"DeadByDaylight"/"Content"/"Paks"/"Paks"]:
        if c.exists() and c.is_dir(): return c
    for c in root.rglob("Paks"):
        if c.is_dir() and "deadbydaylight" in str(c).lower(): return c
    return None

def _registry_get_value(key_path, value_name):
    if not _HAS_WINREG: return None
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
        v,_ = winreg.QueryValueEx(key, value_name); return v
    except Exception: return None

def _auto_detect_dbd_paks_path():
    roots = [
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Dead by Daylight"),
        Path(r"C:\Program Files\Steam\steamapps\common\Dead by Daylight"),
        Path(r"D:\SteamLibrary\steamapps\common\Dead by Daylight"),
        Path(r"E:\SteamLibrary\steamapps\common\Dead by Daylight"),
        Path(r"C:\Program Files\Epic Games\DeadByDaylight"),
        Path(r"D:\Program Files\Epic Games\DeadByDaylight"),
        Path(r"C:\XboxGames\Dead by Daylight"),
        Path(r"D:\XboxGames\Dead by Daylight"),
    ]
    for root in roots:
        if root.exists():
            paks = _find_paks_from_game_root(root)
            if paks: return (str(paks), f"Auto-detected: {root}")
    if _HAS_WINREG:
        sp = _registry_get_value(r"SOFTWARE\WOW6432Node\Valve\Steam","InstallPath")
        if sp:
            g = Path(sp)/"steamapps"/"common"/"Dead by Daylight"
            if g.exists():
                paks = _find_paks_from_game_root(g)
                if paks: return (str(paks), "Auto-detected from Steam registry")
    return (None, "Could not auto-detect Dead by Daylight install folder")

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_ROOT=BG_PANEL=BG_CARD=BG_CARD_HOV=BG_FIELD=ACCENT=ACCENT_DIM=None
GREEN=RED=ORANGE=TEXT_PRI=TEXT_SEC=TEXT_MUT=None

def _init_theme():
    global BG_ROOT,BG_PANEL,BG_CARD,BG_CARD_HOV,BG_FIELD,ACCENT,ACCENT_DIM
    global GREEN,RED,ORANGE,TEXT_PRI,TEXT_SEC,TEXT_MUT
    BG_ROOT="#0d0d10"; BG_PANEL="#13131a"; BG_CARD="#1c1c25"; BG_CARD_HOV="#22222e"
    BG_FIELD="#0f0f16"; ACCENT="#c084fc"; ACCENT_DIM="#7c3aed"; GREEN="#4ade80"
    RED="#f87171"; ORANGE="#fb923c"; TEXT_PRI="#f0f0ff"; TEXT_SEC="#7070a0"; TEXT_MUT="#3a3a5c"

_init_theme()

_MAX_NAME_CHARS = 22
def _truncate(name, limit=_MAX_NAME_CHARS):
    return name if len(name) <= limit else name[:limit-1]+"…"

_FL=_FLS=_FSM=_FB=_FBM=_FBB=_FT=_FBL=_FM=_FD=_FP=_FST = None

def _init_fonts():
    global _FL,_FLS,_FSM,_FB,_FBM,_FBB,_FT,_FBL,_FM,_FD,_FP,_FST
    _FL  = ctk.CTkFont(family="Segoe UI Black",size=24,weight="bold")
    _FLS = ctk.CTkFont(family="Segoe UI",size=15,weight="bold")
    _FSM = ctk.CTkFont(family="Segoe UI",size=9,weight="bold")
    _FB  = ctk.CTkFont(family="Segoe UI",size=11)
    _FBM = ctk.CTkFont(family="Segoe UI",size=12)
    _FBB = ctk.CTkFont(family="Segoe UI",size=13,weight="bold")
    _FT  = ctk.CTkFont(family="Segoe UI Black",size=24,weight="bold")
    _FBL = ctk.CTkFont(family="Segoe UI",size=14,weight="bold")
    _FM  = ctk.CTkFont(family="Consolas",size=12)
    _FD  = ctk.CTkFont(size=7)
    _FP  = ctk.CTkFont(size=11)
    _FST = ctk.CTkFont(family="Segoe UI",size=11)

# ── ModCard (with multi‑select and context menu) ──────────────────────────────
class ModCard(ctk.CTkFrame):
    def __init__(self, master, folder_name, on_select, on_rename, on_context_menu, **kw):
        super().__init__(master,fg_color=BG_CARD,corner_radius=6,height=30,**kw)
        self.pack_propagate(False)
        self._normal=BG_CARD; self._hovered=BG_CARD_HOV; self._sel_col="#1e1830"
        self._is_sel=False; self.folder_name=folder_name
        self.on_select=on_select
        self.on_rename=on_rename
        self.on_context_menu=on_context_menu
        self._build()
        for w in [self,self._nlbl,self._stripe,self._dot]:
            w.bind("<Button-1>", self._click)
            w.bind("<Button-3>", self._right_click)
            w.bind("<Enter>",self._on_enter)
            w.bind("<Leave>",self._on_leave)

    def _build(self):
        self._stripe=ctk.CTkFrame(self,width=3,corner_radius=2,fg_color=ACCENT_DIM)
        self._stripe.pack(side="left",fill="y",padx=(6,0),pady=3)
        self._nlbl=ctk.CTkLabel(self,text=_truncate(self.folder_name),
                                font=_FB,text_color=TEXT_PRI,anchor="w")
        self._nlbl.pack(side="left",fill="x",expand=True,padx=(8,2),pady=4)
        self._dot=ctk.CTkLabel(self,text="●",width=14,font=_FD,text_color=TEXT_MUT)
        self._dot.pack(side="right",padx=(0,6))
        self._pen=ctk.CTkButton(self,text="✏",width=22,height=22,fg_color="transparent",
                                hover_color="#2a2040",text_color=ACCENT,font=_FP,
                                corner_radius=4,command=self._rename)

    def _on_enter(self,_=None):
        if not self._is_sel: self.configure(fg_color=self._hovered)
        self._pen.pack(side="right",padx=(0,2),before=self._dot)

    def _on_leave(self,_=None):
        if not self._is_sel: self.configure(fg_color=self._normal)
        try:
            x,y=self.winfo_pointerxy(); wx,wy=self.winfo_rootx(),self.winfo_rooty()
            if not (wx<=x<=wx+self.winfo_width() and wy<=y<=wy+self.winfo_height()):
                self._pen.pack_forget()
        except Exception: self._pen.pack_forget()

    def _click(self, event):
        self.on_select(self.folder_name, event)

    def _right_click(self, event):
        self.on_context_menu(self.folder_name, event)

    def _rename(self):
        self.on_rename(self.folder_name)

    def update_name(self,n):
        self.folder_name=n; self._nlbl.configure(text=_truncate(n))

    def set_selected(self,s):
        self._is_sel=s
        if s:
            self.configure(fg_color=self._sel_col)
            self._stripe.configure(fg_color=ACCENT)
            self._nlbl.configure(text_color=ACCENT)
        else:
            self.configure(fg_color=self._normal)
            self._stripe.configure(fg_color=ACCENT_DIM)
            self._nlbl.configure(text_color=TEXT_PRI)

    def set_installed(self,v): self._dot.configure(text_color=GREEN if v else TEXT_MUT)

# ── PathEditorPanel (unchanged) ───────────────────────────────────────────────
class PathEditorPanel(ctk.CTkFrame):
    _SUFFIXES = ["-Windows", "-EGS", "-WinGDK"]

    def __init__(self, master, custom_paths, builtin_platforms, on_save, on_close):
        super().__init__(master, fg_color=BG_ROOT, corner_radius=0)
        self._paths      = [dict(p) for p in custom_paths]
        self._builtins   = builtin_platforms
        self._on_save    = on_save
        self._on_close   = on_close
        self._rows       = []
        self._build()
        self._render_rows()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color=BG_PANEL, height=52, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkLabel(top, text="⚙  Path Editor",
                     font=ctk.CTkFont(family="Segoe UI Black", size=16, weight="bold"),
                     text_color=ACCENT).pack(side="left", padx=16, pady=10)

        ctk.CTkButton(top, text="✕  Close", width=100, height=34,
                      fg_color=BG_CARD, hover_color="#3a1a1a",
                      border_width=1, border_color=RED, text_color=RED,
                      corner_radius=8, font=ctk.CTkFont(family="Segoe UI", size=12),
                      command=self._close).pack(side="right", padx=12, pady=10)

        ctk.CTkButton(top, text="💾  Save", width=90, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
                      corner_radius=8, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                      command=self._save).pack(side="right", padx=(0,6), pady=10)

        ctk.CTkButton(top, text="＋  Add Path", width=110, height=34,
                      fg_color=BG_CARD, hover_color=BG_CARD_HOV,
                      border_width=1, border_color=ACCENT_DIM, text_color=ACCENT,
                      corner_radius=8, font=ctk.CTkFont(family="Segoe UI", size=12),
                      command=self._add_row).pack(side="right", padx=(0,6), pady=10)

        ch = ctk.CTkFrame(self, fg_color=BG_PANEL, height=32, corner_radius=0)
        ch.pack(fill="x")
        ch.pack_propagate(False)
        ctk.CTkLabel(ch, text="", width=44).pack(side="left", padx=(16,0))
        ctk.CTkLabel(ch, text="NAME", font=_FSM, text_color=TEXT_MUT,
                     width=180, anchor="w").pack(side="left", padx=(0,8))
        ctk.CTkLabel(ch, text="PAKS PATH", font=_FSM, text_color=TEXT_MUT,
                     anchor="w").pack(side="left", fill="x", expand=True, padx=(0,8))
        ctk.CTkLabel(ch, text="SUFFIX", font=_FSM, text_color=TEXT_MUT,
                     width=130, anchor="w").pack(side="left", padx=(0,8))
        ctk.CTkLabel(ch, text="", width=36).pack(side="left", padx=(0,16))

        ctk.CTkLabel(self, text="BUILT-IN PLATFORMS", font=_FSM,
                     text_color=TEXT_MUT).pack(anchor="w", padx=24, pady=(14, 4))
        for name, entry in self._builtins.items():
            self._make_builtin_row(name, entry)

        ctk.CTkFrame(self, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=20, pady=(12,0))
        ctk.CTkLabel(self, text="CUSTOM PATHS", font=_FSM,
                     text_color=TEXT_MUT).pack(anchor="w", padx=24, pady=(10, 4))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                               scrollbar_button_color=TEXT_MUT,
                                               scrollbar_button_hover_color=TEXT_SEC)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    def _make_builtin_row(self, name, entry):
        row = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=7, height=38)
        row.pack(fill="x", padx=20, pady=2)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text="🔒", width=28, font=_FP,
                     text_color=TEXT_MUT).pack(side="left", padx=(10, 4))
        ctk.CTkLabel(row, text=name, font=_FB, text_color=TEXT_SEC,
                     width=180, anchor="w").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text=entry.get("path", ""), font=_FB,
                     text_color=TEXT_MUT, anchor="w").pack(side="left", fill="x",
                     expand=True, padx=(0, 8))
        ctk.CTkLabel(row, text=entry.get("suffix", ""), font=_FB,
                     text_color=TEXT_MUT, width=130, anchor="w").pack(side="left", padx=(0, 8))

    def _render_rows(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows.clear()
        for i, entry in enumerate(self._paths):
            self._add_row_widget(i, entry)

    def _add_row_widget(self, i, entry):
        bg = BG_CARD if i % 2 == 0 else BG_FIELD
        row_frame = ctk.CTkFrame(self._scroll, fg_color=bg, corner_radius=7, height=42)
        row_frame.pack(fill="x", pady=3)
        row_frame.pack_propagate(False)

        edit_btn = ctk.CTkButton(row_frame, text="✏", width=28, height=28,
                                  fg_color="transparent", hover_color="#2a2040",
                                  text_color=ACCENT, font=_FP, corner_radius=4,
                                  command=lambda idx=i: self._toggle_edit(idx))
        edit_btn.pack(side="left", padx=(10, 4))

        name_var = ctk.StringVar(value=entry.get("name", ""))
        name_ent = ctk.CTkEntry(row_frame, textvariable=name_var, width=180, height=30,
                                 fg_color=BG_FIELD, text_color=TEXT_PRI,
                                 border_color=TEXT_MUT, corner_radius=6, font=_FB,
                                 state="disabled")
        name_ent.pack(side="left", padx=(0, 8))

        path_var = ctk.StringVar(value=entry.get("path", ""))
        path_ent = ctk.CTkEntry(row_frame, textvariable=path_var, height=30,
                                 fg_color=BG_FIELD, text_color=TEXT_PRI,
                                 border_color=TEXT_MUT, corner_radius=6, font=_FB,
                                 state="disabled")
        path_ent.pack(side="left", fill="x", expand=True, padx=(0, 4))

        browse_btn = ctk.CTkButton(row_frame, text="📂", width=30, height=30,
                                    fg_color="transparent", hover_color=BG_CARD_HOV,
                                    text_color=TEXT_MUT, font=_FP, corner_radius=4,
                                    state="disabled",
                                    command=lambda pv=path_var: self._browse(pv))
        browse_btn.pack(side="left", padx=(0, 8))

        suf_var = ctk.StringVar(value=entry.get("suffix", "-Windows"))
        suf_menu = ctk.CTkOptionMenu(row_frame, values=self._SUFFIXES,
                                      variable=suf_var, width=130, height=30,
                                      fg_color=BG_FIELD, button_color=ACCENT_DIM,
                                      button_hover_color=ACCENT,
                                      dropdown_fg_color=BG_CARD,
                                      dropdown_hover_color=BG_CARD_HOV,
                                      font=_FB, text_color=TEXT_PRI, corner_radius=6,
                                      state="disabled")
        suf_menu.pack(side="left", padx=(0, 8))

        del_btn = ctk.CTkButton(row_frame, text="🗑", width=28, height=28,
                                 fg_color="transparent", hover_color="#3a1a1a",
                                 text_color=RED, font=_FP, corner_radius=4,
                                 command=lambda idx=i: self._delete_row(idx))
        del_btn.pack(side="left", padx=(0, 10))

        self._rows.append({
            "frame": row_frame, "edit_btn": edit_btn,
            "name_var": name_var, "name_ent": name_ent,
            "path_var": path_var, "path_ent": path_ent,
            "browse_btn": browse_btn,
            "suf_var": suf_var, "suf_menu": suf_menu,
        })

    def _toggle_edit(self, idx):
        if idx >= len(self._rows): return
        row = self._rows[idx]
        editing = row["name_ent"].cget("state") == "disabled"
        state = "normal" if editing else "disabled"
        row["name_ent"].configure(state=state)
        row["path_ent"].configure(state=state)
        row["browse_btn"].configure(state=state)
        row["suf_menu"].configure(state=state)
        row["edit_btn"].configure(
            text_color=ORANGE if editing else ACCENT,
            fg_color="#1a1020" if editing else "transparent")
        if not editing:
            self._flush(idx)

    def _flush(self, idx):
        if idx >= len(self._rows) or idx >= len(self._paths): return
        r = self._rows[idx]
        self._paths[idx] = {
            "name":   r["name_var"].get().strip() or f"Path {idx+1}",
            "path":   r["path_var"].get().strip(),
            "suffix": r["suf_var"].get(),
        }

    def _flush_all(self):
        for i in range(len(self._rows)):
            self._flush(i)

    def _add_row(self):
        self._paths.append({"name": "New Path", "path": "", "suffix": "-Windows"})
        self._render_rows()
        self._toggle_edit(len(self._paths) - 1)

    def _delete_row(self, idx):
        if idx >= len(self._paths): return
        del self._paths[idx]
        self._render_rows()

    def _browse(self, path_var):
        folder = filedialog.askdirectory(title="Select Paks Folder")
        if folder:
            path_var.set(folder)

    def _save(self):
        self._flush_all()
        clean = [p for p in self._paths
                 if p.get("name","").strip() and p.get("path","").strip()]
        self._on_save(clean)

    def _close(self):
        self._on_close()

# ── ShareDialog (unchanged) ───────────────────────────────────────────────────
class ShareDialog(ctk.CTkToplevel):
    def __init__(self,master,mod_name,mod_path):
        super().__init__(master)
        self.title("Share Mod"); self.geometry("520x300")
        self.resizable(False,False); self.grab_set()
        self.configure(fg_color=BG_PANEL)
        self.mod_name=mod_name; self.mod_path=mod_path; self._link=""
        self._build()
        threading.Thread(target=self._zip_and_upload,daemon=True).start()

    def _build(self):
        ctk.CTkLabel(self,text="📤  Share Mod",
                     font=ctk.CTkFont(family="Segoe UI Black",size=18,weight="bold"),
                     text_color=ACCENT).pack(pady=(24,4))
        ctk.CTkLabel(self,text=self.mod_name,
                     font=ctk.CTkFont(family="Segoe UI",size=12),
                     text_color=TEXT_SEC).pack()
        ctk.CTkFrame(self,height=1,fg_color=TEXT_MUT).pack(fill="x",padx=24,pady=16)
        self._slbl=ctk.CTkLabel(self,text="⏳  Zipping mod…",
                                font=ctk.CTkFont(family="Segoe UI",size=12),
                                text_color=TEXT_PRI)
        self._slbl.pack(pady=(0,10))
        self._prog=ctk.CTkProgressBar(self,width=460,height=8,fg_color=BG_FIELD,progress_color=ACCENT)
        self._prog.pack(pady=(0,16)); self._prog.set(0)
        self._prog.configure(mode="indeterminate"); self._prog.start()
        self._lf=ctk.CTkFrame(self,fg_color="transparent")
        self._le=ctk.CTkEntry(self._lf,width=340,height=36,fg_color=BG_FIELD,text_color=TEXT_PRI,
                              border_color=ACCENT_DIM,corner_radius=8,
                              font=ctk.CTkFont(family="Consolas",size=12),state="readonly")
        self._le.pack(side="left",padx=(0,8))
        self._cb=ctk.CTkButton(self._lf,text="📋 Copy",width=90,height=36,
                               fg_color=ACCENT,hover_color=ACCENT_DIM,text_color="black",
                               corner_radius=8,font=ctk.CTkFont(family="Segoe UI",size=12,weight="bold"),
                               command=self._copy_link)
        self._cb.pack(side="left")
        self._note=ctk.CTkLabel(self,text="Links never expire  •  powered by GoFile.io",
                                font=ctk.CTkFont(family="Segoe UI",size=10),text_color=TEXT_MUT)
        ctk.CTkButton(self,text="Close",width=100,height=34,fg_color=BG_CARD,
                      hover_color=BG_CARD_HOV,text_color=TEXT_PRI,corner_radius=8,
                      command=self.destroy).pack(side="bottom",pady=(0,16))

    def _zip_and_upload(self):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                zp=os.path.join(tmp,f"{self.mod_name}.zip")
                with zipfile.ZipFile(zp,"w",zipfile.ZIP_STORED) as zf:
                    for fn in os.listdir(self.mod_path):
                        fp=os.path.join(self.mod_path,fn)
                        if os.path.isfile(fp): zf.write(fp,fn)
                zs=os.path.getsize(zp)
                self.after(0,lambda:self._slbl.configure(text="⏳  Getting upload server…"))
                ctx=ssl.create_default_context()
                info=_https_get_json("api.gofile.io","/servers")
                if info.get("status")!="ok": raise RuntimeError("GoFile: could not get server list")
                server=info["data"]["servers"][0]["name"]
                self.after(0,lambda:self._slbl.configure(text=f"⬆️  Uploading {_format_bytes(zs)}…"))
                self.after(0,lambda:self._prog.configure(mode="determinate"))
                self.after(0,self._prog.stop)
                self.after(0,lambda:self._prog.set(0))
                boundary=b"DBDPakBoundary"
                zname=f"{self.mod_name}.zip".encode()
                hdr=(b"--"+boundary+b"\r\n"
                     b'Content-Disposition: form-data; name="file"; filename="'+zname+b'"\r\n'
                     b"Content-Type: application/zip\r\n\r\n")
                ftr=b"\r\n--"+boundary+b"--\r\n"
                total=len(hdr)+zs+len(ftr)
                conn=http.client.HTTPSConnection(f"{server}.gofile.io",timeout=120,context=ctx)
                conn.putrequest("POST","/contents/uploadfile")
                conn.putheader("Content-Type",f"multipart/form-data; boundary={boundary.decode()}")
                conn.putheader("Content-Length",str(total))
                conn.putheader("User-Agent","DBDPakLoader/1.0")
                conn.putheader("Connection","close")
                conn.endheaders(); conn.send(hdr)
                sent=len(hdr); t0=tl=time.monotonic()
                with open(zp,"rb") as fh:
                    while True:
                        chunk=fh.read(65536)
                        if not chunk: break
                        conn.send(chunk); sent+=len(chunk)
                        now=time.monotonic()
                        if now-tl>=0.1:
                            frac=min(sent/total,1.0); spd=sent/max(now-t0,0.001)
                            txt=f"⬆️  {int(frac*100)}%  •  {_format_bytes(int(spd))}/s"
                            self.after(0,lambda t=txt:self._slbl.configure(text=t))
                            self.after(0,lambda f=frac:self._prog.set(f))
                            tl=now
                conn.send(ftr)
                self.after(0,lambda:self._slbl.configure(text="⏳  Waiting for server…"))
                self.after(0,lambda:self._prog.set(1.0))
                result=json.loads(conn.getresponse().read().decode()); conn.close()
                if result.get("status")!="ok":
                    raise RuntimeError(f"GoFile: {result.get('message',str(result))}")
                self.after(0,lambda:self._on_success(result["data"]["downloadPage"]))
        except Exception as e:
            self.after(0,lambda:self._on_error(str(e)))

    def _on_success(self,link):
        self._link=link
        self._prog.stop()
        self._prog.configure(mode="determinate",progress_color=GREEN); self._prog.set(1.0)
        self._slbl.configure(text="✅  Upload complete! Share this link:",text_color=GREEN)
        self._le.configure(state="normal"); self._le.insert(0,link); self._le.configure(state="readonly")
        self._lf.pack(pady=(0,8)); self._note.pack(pady=(0,4))
        self.clipboard_clear(); self.clipboard_append(link)

    def _on_error(self,msg):
        self._prog.stop(); self._prog.pack_forget()
        self._slbl.configure(text=f"❌  {msg}",text_color=RED,wraplength=460,justify="center")

    def _copy_link(self):
        if self._link:
            self.clipboard_clear(); self.clipboard_append(self._link)
            self._cb.configure(text="✅ Copied!")
            self.after(2000,lambda:self._cb.configure(text="📋 Copy"))

# ── ModBrowserPanel (unchanged) ───────────────────────────────────────────────
class ModBrowserPanel(ctk.CTkFrame):
    CW = 230
    CH = 330

    def __init__(self, master, mods_dir, on_install_cb, on_close_cb):
        super().__init__(master, fg_color=BG_ROOT, corner_radius=0)
        self._mods_dir = mods_dir
        self._on_install = on_install_cb
        self._on_close = on_close_cb
        self._all = []
        self._filtered = []
        self._img_cache = {}
        self._installing = set()
        self._search_id = None
        self._current_mod = None
        self._page = 0
        self._page_size = 12
        self._build()
        threading.Thread(target=self._fetch_catalog, daemon=True).start()

    def _build(self):
        self._top = ctk.CTkFrame(self, fg_color=BG_PANEL, height=52, corner_radius=0)
        self._top.pack(fill="x")
        self._top.pack_propagate(False)

        self._back_btn = ctk.CTkButton(
            self._top, text="← Back", width=80, height=34,
            fg_color="transparent", hover_color=BG_CARD_HOV,
            border_width=1, border_color=TEXT_MUT, text_color=TEXT_PRI,
            corner_radius=8, font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._show_grid)

        self._top_title = ctk.CTkLabel(
            self._top, text="🌐  Mod Browser",
            font=ctk.CTkFont(family="Segoe UI Black", size=16, weight="bold"),
            text_color=ACCENT)
        self._top_title.pack(side="left", padx=16, pady=10)

        ctk.CTkButton(
            self._top, text="✕ Close Browser", width=120, height=34,
            fg_color=BG_CARD, hover_color="#3a1a1a",
            border_width=1, border_color=RED, text_color=RED,
            corner_radius=8, command=self._on_close
        ).pack(side="right", padx=12, pady=10)

        self._sort_var = ctk.StringVar(value="Most Downloaded")
        self._sort_menu = ctk.CTkOptionMenu(
            self._top, values=["Most Downloaded","Newest","Top Rated","A–Z"],
            variable=self._sort_var, command=self._apply_sort,
            width=175, height=34,
            fg_color=BG_FIELD, button_color=ACCENT_DIM, button_hover_color=ACCENT,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOV,
            font=ctk.CTkFont(family="Segoe UI", size=12), text_color=TEXT_PRI, corner_radius=7)
        self._sort_menu.pack(side="right", padx=(0,6), pady=10)

        self._sv = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            self._top, textvariable=self._sv,
            placeholder_text="Search mods…", width=260, height=34,
            fg_color=BG_FIELD, text_color=TEXT_PRI,
            border_color=TEXT_MUT, corner_radius=9,
            font=ctk.CTkFont(family="Segoe UI", size=12))
        self._search_entry.pack(side="right", padx=(0,6), pady=10)
        self._search_entry.bind("<KeyRelease>", self._on_search)

        self._count_lbl = ctk.CTkLabel(
            self._top, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color=TEXT_MUT)
        self._count_lbl.pack(side="left", padx=6)

        self._pages = ctk.CTkFrame(self, fg_color="transparent")
        self._pages.pack(fill="both", expand=True)

        self._grid_page = ctk.CTkFrame(self._pages, fg_color="transparent")
        self._grid_page.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._loading_lbl = ctk.CTkLabel(
            self._grid_page, text="⏳  Loading mods from Cloudflare D1...",
            font=ctk.CTkFont(family="Segoe UI", size=14), text_color=TEXT_SEC)
        self._loading_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self._scroll = ctk.CTkScrollableFrame(
            self._grid_page, fg_color="transparent",
            scrollbar_button_color=TEXT_MUT, scrollbar_button_hover_color=TEXT_SEC)

        self._pag_bar = ctk.CTkFrame(self._grid_page, fg_color=BG_PANEL, height=46, corner_radius=0)
        self._prev_btn = ctk.CTkButton(
            self._pag_bar, text="◀", width=40, height=32,
            fg_color=BG_CARD, hover_color=BG_CARD_HOV,
            text_color=TEXT_PRI, corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._prev_page)
        self._prev_btn.pack(side="left", padx=(16,6), pady=7)
        self._page_lbl = ctk.CTkLabel(
            self._pag_bar, text="Page 1 of 1",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_PRI)
        self._page_lbl.pack(side="left", padx=8)
        self._next_btn = ctk.CTkButton(
            self._pag_bar, text="▶", width=40, height=32,
            fg_color=BG_CARD, hover_color=BG_CARD_HOV,
            text_color=TEXT_PRI, corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._next_page)
        self._next_btn.pack(side="left", padx=(6,0), pady=7)

        self._mod_page = ctk.CTkFrame(self._pages, fg_color="transparent")

    def _fetch_catalog(self):
        try:
            ctx  = ssl.create_default_context()
            conn = http.client.HTTPSConnection(_WORKER_HOST, timeout=20, context=ctx)
            conn.request("GET", f"/mods?hwid={_HWID}",
                         headers={"User-Agent": "DBDPakLoader/1.0"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"Worker returned HTTP {resp.status}")
            data = json.loads(resp.read().decode())
            conn.close()
            self._all = data.get("mods", data) if isinstance(data, dict) else data
            self.after(0, self._catalog_ready)
        except Exception as e:
            self.after(0, lambda e=e: self._loading_lbl.configure(
                text=f"❌  Could not load mods from D1:\n{str(e)}",
                text_color=RED, wraplength=400))

    def _catalog_ready(self):
        self._loading_lbl.place_forget()
        self._filtered = list(self._all)
        self._apply_sort()
        self._scroll.pack(fill="both", expand=True, padx=8, pady=(8,0))
        self._pag_bar.pack(fill="x")
        self._render_grid()

    def _on_search(self, _=None):
        if self._search_id: self.after_cancel(self._search_id)
        self._search_id = self.after(180, self._do_search)

    def _do_search(self):
        q = self._sv.get().strip().lower()
        self._filtered = (
            [m for m in self._all
             if q in m.get("name","").lower()
             or q in m.get("description","").lower()
             or q in m.get("author","").lower()
             or q in " ".join(m.get("tags",[])).lower()]
            if q else list(self._all))
        self._page = 0
        self._apply_sort()

    def _apply_sort(self, _=None):
        mode = self._sort_var.get()
        keys = {
            "Most Downloaded": lambda m: m.get("downloads", 0),
            "Newest":          lambda m: m.get("added", ""),
            "Top Rated":       lambda m: m.get("likes", 0) - m.get("dislikes", 0),
            "A–Z":             lambda m: m.get("name","").lower(),
        }
        self._filtered.sort(key=keys.get(mode, keys["A–Z"]), reverse=mode != "A–Z")
        self._page = 0
        self._render_grid()

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._render_grid()

    def _next_page(self):
        total_pages = max(1, (len(self._filtered) + self._page_size - 1) // self._page_size)
        if self._page < total_pages - 1:
            self._page += 1
            self._render_grid()

    def _show_grid(self):
        self._mod_page.place_forget()
        self._grid_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._back_btn.pack_forget()
        self._top_title.configure(text="🌐  Mod Browser")
        self._search_entry.pack(side="right", padx=(0,6), pady=10)
        self._sort_menu.pack(side="right", padx=(0,6), pady=10)
        self._count_lbl.pack(side="left", padx=6)
        self._render_grid()

    def _show_mod_page(self, mod):
        self._current_mod = mod
        self._grid_page.place_forget()
        self._sort_menu.pack_forget()
        self._search_entry.pack_forget()
        self._count_lbl.pack_forget()
        self._back_btn.pack(side="left", padx=(6,0), pady=10, before=self._top_title)
        self._top_title.configure(text=f"🌐  {mod.get('name','')}")
        self._build_mod_page(mod)
        self._mod_page.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _render_grid(self):
        for w in self._scroll.winfo_children(): w.destroy()
        n = len(self._filtered)
        self._count_lbl.configure(text=f"{n} mod{'s' if n!=1 else ''}")

        total_pages = max(1, (n + self._page_size - 1) // self._page_size)
        self._page = max(0, min(self._page, total_pages - 1))
        self._page_lbl.configure(text=f"Page {self._page+1} of {total_pages}")
        self._prev_btn.configure(state="normal" if self._page > 0 else "disabled")
        self._next_btn.configure(state="normal" if self._page < total_pages - 1 else "disabled")

        if not self._filtered:
            ctk.CTkLabel(self._scroll, text="No mods found.",
                         font=ctk.CTkFont(family="Segoe UI", size=14),
                         text_color=TEXT_SEC).pack(pady=60)
            return

        start = self._page * self._page_size
        page_mods = self._filtered[start:start + self._page_size]

        cols = 4
        for i, mod in enumerate(page_mods):
            if i % cols == 0:
                row = ctk.CTkFrame(self._scroll, fg_color="transparent")
                row.pack(fill="x", pady=5, padx=6)
            self._make_card(row, mod)

    def _make_card(self, parent, mod):
        mid = mod.get("id", mod.get("name",""))
        name = mod.get("name","Unnamed")
        content_id = mod.get("content_id", "")
        already = self._is_installed(content_id, name)
        busy = mid in self._installing
        likes = mod.get("likes", 0)
        dislikes = mod.get("dislikes", 0)
        total = likes + dislikes
        pct = (likes / total) if total > 0 else 1.0
        has_download = bool(mod.get("download_url"))

        card_fg = BG_CARD if has_download else "#0f0f16"
        title_color = TEXT_PRI if has_download else TEXT_MUT
        author_color = TEXT_SEC if has_download else TEXT_MUT
        card = ctk.CTkFrame(parent, fg_color=card_fg, corner_radius=10,
                    width=self.CW, height=self.CH)
        card.pack(side="left", padx=6)
        card.pack_propagate(False)

        tf = ctk.CTkFrame(card, fg_color=BG_FIELD, corner_radius=8,
                          width=self.CW-20, height=145)
        tf.pack(padx=10, pady=(10,0))
        tf.pack_propagate(False)
        tl = ctk.CTkLabel(tf, text="🖼", font=_FT, text_color=TEXT_MUT)
        tl.place(relx=0.5, rely=0.5, anchor="center")
        img_url = mod.get("image","")
        if img_url and _HAS_PIL:
            threading.Thread(target=self._load_thumb,
                             args=(img_url, tl, self.CW-20, 145), daemon=True).start()
        if already:
            try:
                badge = ctk.CTkLabel(tf, text="✔ Installed", font=_FSM,
                                     fg_color=GREEN, text_color="black",
                                     corner_radius=6)
                badge.place(x=8, y=8)
                try:
                    self.after(50, lambda b=badge: b.lift())
                    self.after(300, lambda b=badge: b.lift())
                except Exception:
                    badge.lift()
            except Exception:
                pass
        ctk.CTkLabel(card, text=name,
                     font=_FBL,
                     text_color=title_color, anchor="w", wraplength=self.CW-20
                     ).pack(anchor="w", padx=10, pady=(8,0))
        ctk.CTkLabel(card, text=f"by {mod.get('author','unknown')}",
                     font=_FB,
                     text_color=author_color, anchor="w").pack(anchor="w", padx=10)

        bar_bg = ctk.CTkFrame(card, fg_color=RED, corner_radius=3, height=5)
        bar_bg.pack(fill="x", padx=10, pady=(6,0))
        bar_bg.pack_propagate(False)
        ctk.CTkFrame(bar_bg, fg_color=GREEN, corner_radius=3, height=5
                     ).place(relx=0, rely=0, relwidth=pct, relheight=1.0)

        sr = ctk.CTkFrame(card, fg_color="transparent")
        sr.pack(anchor="w", padx=10, pady=(3,0))
        stats_txt = f"👍 {likes:,}   👎 {dislikes:,}   ⬇ {mod.get('downloads',0):,}"
        ctk.CTkLabel(sr, text=stats_txt, font=_FB, text_color=TEXT_MUT).pack(side="left")

        tags = mod.get("tags",[])
        if tags:
            tr = ctk.CTkFrame(card, fg_color="transparent")
            tr.pack(anchor="w", padx=8, pady=(4,0))
            for t in tags[:3]:
                ctk.CTkLabel(tr, text=t,
                             font=_FSM,
                             fg_color="#1e1a2e", text_color=ACCENT,
                             corner_radius=4, padx=5, pady=1
                             ).pack(side="left", padx=2)

        ctk.CTkButton(card, text="👁  View Mod", height=30,
                  fg_color=BG_FIELD, hover_color=BG_CARD_HOV,
                  border_width=1, border_color=TEXT_MUT, text_color=TEXT_PRI,
                  corner_radius=7, font=_FB,
                  command=lambda m=mod: self._show_mod_page(m)
                  ).pack(fill="x", padx=10, pady=(8,4))

        if busy:
            ctk.CTkLabel(card, text="⏳ Installing…",
                         font=_FB,
                         text_color=ORANGE).pack(padx=10, anchor="w")
        elif already:
            ctk.CTkLabel(card, text="✅ Installed",
                         font=_FB,
                         text_color=GREEN).pack(padx=10, anchor="w")
        else:
            if has_download:
                ctk.CTkButton(card, text="⬇ Install", height=30,
                              fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
                              corner_radius=7,
                              font=_FB,
                              command=lambda m=mod: self._start_install(m)
                              ).pack(fill="x", padx=10, pady=(0,8))
            else:
                ctk.CTkButton(card, text="⬇ Install", height=30,
                              fg_color=BG_CARD, hover_color=BG_CARD, text_color=TEXT_MUT,
                              corner_radius=7, state="disabled", font=_FB
                              ).pack(fill="x", padx=10, pady=(0,8))

        for w in [card, tf, tl]:
            w.bind("<Button-1>", lambda e, m=mod: self._show_mod_page(m))
            if has_download:
                w.bind("<Enter>",    lambda e, c=card: c.configure(fg_color=BG_CARD_HOV))
                w.bind("<Leave>",    lambda e, c=card: c.configure(fg_color=BG_CARD))

    def _load_thumb(self, url, label, w, h):
        ck = f"{url}_{w}x{h}"
        if ck in self._img_cache:
            img = self._img_cache[ck]
            if img: self.after(0, lambda: label.configure(image=img, text=""))
            return
        try:
            data = _https_get_bytes(url)
            ip = Image.open(io.BytesIO(data)).convert("RGB").resize((w,h), Image.LANCZOS)
            img = ctk.CTkImage(light_image=ip, dark_image=ip, size=(w,h))
            self._img_cache[ck] = img
            self.after(0, lambda: label.configure(image=img, text=""))
        except Exception:
            self._img_cache[ck] = None

    def _build_mod_page(self, mod):
        for w in self._mod_page.winfo_children(): w.destroy()
        mid = mod.get("id", mod.get("name",""))
        likes = mod.get("likes", 0)
        dislikes = mod.get("dislikes", 0)
        total = likes + dislikes
        pct = (likes / total) if total > 0 else 1.0
        vote = mod.get("user_vote")
        content_id = mod.get("content_id", "")
        already = self._is_installed(content_id, mod.get("name",""))
        busy = mid in self._installing
        has_download = bool(mod.get("download_url"))

        body = ctk.CTkFrame(self._mod_page, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=20)

        left = ctk.CTkFrame(body, fg_color="transparent", width=380)
        left.pack(side="left", fill="y", padx=(0,24))
        left.pack_propagate(False)

        img_h = 220
        img_frame = ctk.CTkFrame(left, fg_color=BG_FIELD, corner_radius=12,
                                 width=380, height=img_h)
        img_frame.pack(fill="x")
        img_frame.pack_propagate(False)
        self._detail_img_lbl = ctk.CTkLabel(
            img_frame, text="🖼", font=ctk.CTkFont(size=52), text_color=TEXT_MUT)
        self._detail_img_lbl.place(relx=0.5, rely=0.5, anchor="center")
        img_url = mod.get("image","")
        if img_url and _HAS_PIL:
            threading.Thread(target=self._load_thumb,
                             args=(img_url, self._detail_img_lbl, 380, img_h),
                             daemon=True).start()

        vr = ctk.CTkFrame(left, fg_color="transparent")
        vr.pack(fill="x", pady=(14,0))
        up_col = GREEN if vote == "up" else TEXT_SEC
        down_col = RED if vote == "down" else TEXT_SEC
        self._up_btn = ctk.CTkButton(
            vr, text=f"👍 {likes:,}", height=40, width=180,
            fg_color="#0a2218" if vote=="up" else BG_CARD,
            hover_color="#0a2218",
            border_width=2, border_color=up_col, text_color=up_col,
            corner_radius=10, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self._vote(mod, "up"))
        self._up_btn.pack(side="left", padx=(0,8))
        self._down_btn = ctk.CTkButton(
            vr, text=f"👎 {dislikes:,}", height=40, width=180,
            fg_color="#2a0e0e" if vote=="down" else BG_CARD,
            hover_color="#2a0e0e",
            border_width=2, border_color=down_col, text_color=down_col,
            corner_radius=10, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self._vote(mod, "down"))
        self._down_btn.pack(side="left")

        bar_bg = ctk.CTkFrame(left, fg_color=RED, corner_radius=5, height=8)
        bar_bg.pack(fill="x", pady=(10,3))
        bar_bg.pack_propagate(False)
        ctk.CTkFrame(bar_bg, fg_color=GREEN, corner_radius=5, height=8
                     ).place(relx=0, rely=0, relwidth=pct, relheight=1.0)

        pct_row = ctk.CTkFrame(left, fg_color="transparent")
        pct_row.pack(fill="x")
        ctk.CTkLabel(pct_row, text=f"{int(pct*100)}% positive",
                     font=ctk.CTkFont(family="Segoe UI", size=10),
                     text_color=TEXT_SEC).pack(side="left")
        ctk.CTkLabel(pct_row, text=f"{total:,} ratings",
                     font=ctk.CTkFont(family="Segoe UI", size=10),
                     text_color=TEXT_MUT).pack(side="right")

        ctk.CTkLabel(left, text=f"⬇ {mod.get('downloads',0):,} downloads",
                     font=ctk.CTkFont(family="Segoe UI", size=11),
                     text_color=TEXT_MUT).pack(anchor="w", pady=(8,0))

        if busy:
            ctk.CTkLabel(left, text="⏳ Installing…",
                         font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                         text_color=ORANGE).pack(fill="x", pady=(18,0))
        elif already:
            ctk.CTkLabel(left, text="✅ Already installed",
                         font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                         text_color=GREEN).pack(fill="x", pady=(18,0))
        else:
            if has_download:
                ctk.CTkButton(left, text="⬇ Add to My Mods", height=52,
                              font=_FBL,
                              fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
                              corner_radius=12, command=lambda m=mod: self._start_install(m)
                              ).pack(fill="x", pady=(18,0))
            else:
                ctk.CTkButton(left, text="⬇ Add to My Mods", height=52,
                              font=_FBL,
                              fg_color=BG_CARD, hover_color=BG_CARD, text_color=TEXT_MUT,
                              corner_radius=12, state="disabled"
                              ).pack(fill="x", pady=(18,0))

        right = ctk.CTkScrollableFrame(body, fg_color="transparent",
                                        scrollbar_button_color=TEXT_MUT)
        right.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(right, text=mod.get("name",""),
                     font=ctk.CTkFont(family="Segoe UI Black", size=22, weight="bold"),
                     text_color=TEXT_PRI, anchor="w", wraplength=420
                     ).pack(anchor="w", pady=(0,2))
        ctk.CTkLabel(right, text=f"by {mod.get('author','unknown')}",
                     font=ctk.CTkFont(family="Segoe UI", size=12),
                     text_color=TEXT_SEC, anchor="w").pack(anchor="w", pady=(0,10))

        tags = mod.get("tags",[])
        if tags:
            tr = ctk.CTkFrame(right, fg_color="transparent")
            tr.pack(anchor="w", pady=(0,14))
            for t in tags:
                ctk.CTkLabel(tr, text=t,
                             font=_FB,
                             fg_color="#1e1a2e", text_color=ACCENT,
                             corner_radius=5, padx=8, pady=3
                             ).pack(side="left", padx=(0,4))

        ctk.CTkLabel(right, text=mod.get("description","No description provided."),
                     font=_FB,
                     text_color=TEXT_PRI, anchor="w", justify="left", wraplength=420
                     ).pack(anchor="w", pady=(0,18))

        ctk.CTkFrame(right, height=1, fg_color=TEXT_MUT).pack(fill="x", pady=(0,14))

        for label, key in [
            ("Game Version", "game_version"),
            ("Added",        "added"),
            ("Downloads",    "downloads"),
        ]:
            r = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=8)
            r.pack(fill="x", pady=3)
            ctk.CTkLabel(r, text=label,
                         font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                         text_color=TEXT_MUT, width=110, anchor="w"
                         ).pack(side="left", padx=14, pady=10)
            val = mod.get(key, "—")
            if key == "downloads" and isinstance(val, int):
                val = f"{val:,}"
            ctk.CTkLabel(r, text=str(val),
                         font=ctk.CTkFont(family="Segoe UI", size=11),
                         text_color=TEXT_PRI, anchor="w"
                         ).pack(side="left", padx=4, pady=10)

    def _is_installed(self, content_id, display_name):
        mods_dir = self._mods_dir
        if not os.path.isdir(mods_dir):
            return False
        if content_id:
            content_id_lower = content_id.lower()
            for folder in os.listdir(mods_dir):
                fp = os.path.join(mods_dir, folder)
                if not os.path.isdir(fp):
                    continue
                try:
                    for f in os.listdir(fp):
                        stem = os.path.splitext(f)[0].lower()
                        for suf in ("-windows", "-egs", "-wingdk", ""):
                            if suf and stem.endswith(suf):
                                stem = stem[:-len(suf)]
                        if stem == content_id_lower:
                            return True
                except OSError:
                    continue
            return False
        return os.path.isdir(os.path.join(mods_dir, display_name))

    def _vote(self, mod, direction):
        mid      = mod.get("id", mod.get("name",""))
        current  = mod.get("user_vote")

        new_vote = None if current == direction else direction

        old_likes    = mod.get("likes", 0)
        old_dislikes = mod.get("dislikes", 0)

        if current == "up":
            old_likes    = max(0, old_likes - 1)
        elif current == "down":
            old_dislikes = max(0, old_dislikes - 1)

        if new_vote == "up":
            old_likes    += 1
        elif new_vote == "down":
            old_dislikes += 1

        mod["likes"]     = old_likes
        mod["dislikes"]  = old_dislikes
        mod["user_vote"] = new_vote

        for m in self._all:
            if m.get("id", m.get("name","")) == mid:
                m["likes"]     = old_likes
                m["dislikes"]  = old_dislikes
                m["user_vote"] = new_vote
                break

        self._build_mod_page(mod)

        def _send():
            try:
                _worker_post("/vote", {
                    "mod_id":      str(mid),
                    "hwid":        _HWID,
                    "target_vote": new_vote,
                })
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()

    def _start_install(self, mod):
        mid = mod.get("id", mod.get("name",""))
        url = mod.get("download_url","")
        name = mod.get("name","mod")
        if not url:
            messagebox.showerror("No Download", "This mod has no download URL.", parent=self)
            return
        self._installing.add(mid)
        self._render_grid()
        if self._current_mod and self._current_mod.get("id", self._current_mod.get("name")) == mid:
            self._build_mod_page(mod)
        threading.Thread(target=self._install_worker, args=(mod,url,name), daemon=True).start()

    def _install_worker(self, mod, url, mod_name):
        mid = mod.get("id", mod.get("name",""))
        try:
            def _prog(done, total, fname):
                if fname == "Done":
                    return
                txt = f"⬇ Downloading ({done+1}/{total}): {fname}"
                self.after(0, lambda t=txt: self._update_install_status(mid, t))

            if "gofile.io/d/" in url:
                _download_gofile_to_mod(url, mod_name, self._mods_dir, _prog)
                self._installing.discard(mid)
                self.after(0, lambda: self._on_install_success(mod, mod_name))
                return

            data = _https_get_bytes(url)
            _import_mod_from_zip_bytes(data, mod_name, self._mods_dir)
            self._installing.discard(mid)
            self.after(0, lambda: self._on_install_success(mod, mod_name))
        except Exception as e:
            err = str(e)
            self._installing.discard(mid)
            self.after(0, lambda: messagebox.showerror("Install Failed", err, parent=self))
            self.after(0, self._render_grid)

    def _update_install_status(self, mid, text):
        if (self._current_mod and
                self._current_mod.get("id", self._current_mod.get("name")) == mid):
            for w in self._mod_page.winfo_children():
                self._find_and_update_label(w, "⏳ Installing…", text)

    def _find_and_update_label(self, widget, old_text, new_text):
        try:
            if hasattr(widget, "cget") and widget.cget("text") == old_text:
                widget.configure(text=new_text)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._find_and_update_label(child, old_text, new_text)

    def _on_install_success(self, mod, mod_name):
        self._on_install(mod_name)
        mid = mod.get("id", mod.get("name",""))
        def _send():
            try:
                _worker_post("/download", {"mod_id": str(mid), "hwid": _HWID})
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()
        mod["downloads"] = mod.get("downloads", 0) + 1
        for m in self._all:
            if m.get("id", m.get("name","")) == mid:
                m["downloads"] = mod["downloads"]
                break
        self._render_grid()
        if self._current_mod and self._current_mod.get("name") == mod_name:
            self._build_mod_page(mod)

# ── Main App (with multi‑select & context menu, fixed DND binding) ────────────
_BaseClass = TkinterDnD.Tk if _HAS_DND else ctk.CTk

class DBDModLoader(_BaseClass):
    def __init__(self):
        super().__init__()
        _init_fonts()
        self.title("PAK Loader")
        self.geometry("1320x800")
        self.minsize(1100,600)
        self.configure(bg=BG_ROOT)

        _PAK_CONFIG = Path(r"C:\pakConfig")
        self.mods_dir    = str(_PAK_CONFIG / "mods")
        self.config_file = _PAK_CONFIG / "loader_config.json"
        self.custom_game_root = None
        self.custom_paks_path = None
        self.custom_paths = []
        self._builtin_platforms = {
            "Steam (-Windows)":         {"suffix": "-Windows", "path": r"C:\Program Files (x86)\Steam\steamapps\common\Dead by Daylight\DeadByDaylight\Content\Paks"},
            "Epic Games (-EGS)":        {"suffix": "-EGS",     "path": r"C:\Program Files\Epic Games\DeadByDaylight\DeadByDaylight\Content\Paks"},
            "Microsoft Store (-WinGDK)":{"suffix": "-WinGDK",  "path": r"C:\XboxGames\Dead by Daylight\Content\DeadByDaylight\Content\Paks"},
        }
        self.platforms = dict(self._builtin_platforms)
        self.current_mod = None
        self.current_mod_path = None
        self._card_map = {}
        self._paks_cache = None
        self._paks_cache_valid = False
        self._suffix_cache = None
        self._search_after_id = None
        self._latest_version = None

        # Multi‑selection state
        self.selected_mods = set()
        self.last_selected_mod = None
        self.mod_order = []
        self._bulk_frame = None

        try:
            _PAK_CONFIG.mkdir(parents=True, exist_ok=True)
            (_PAK_CONFIG / "mods").mkdir(exist_ok=True)
        except Exception:
            pass

        _OLD_MODS = Path(r"C:\mods")
        _NEW_MODS = _PAK_CONFIG / "mods"
        if _OLD_MODS.exists() and _OLD_MODS.is_dir():
            try:
                for item in _OLD_MODS.iterdir():
                    if item.is_dir():
                        dest = _NEW_MODS / item.name
                        if not dest.exists():
                            shutil.move(str(item), str(dest))
                        else:
                            for f in item.iterdir():
                                if f.is_file() and not (dest / f.name).exists():
                                    shutil.move(str(f), str(dest / f.name))
                remaining = list(_OLD_MODS.iterdir())
                if not remaining:
                    _OLD_MODS.rmdir()
            except Exception:
                pass

        self._load_config()
        self._build_ui()
        self.load_mods()

        if _HAS_DND:
            self.drop_target_register(DND_FILES)
            self.main_area.dnd_bind("<<Drop>>", self._on_drop)
            self._setup_drag_feedback()

        self.after(50, self._attempt_auto_detect)
        threading.Thread(target=self._check_for_update, daemon=True).start()
        self.after(200, self._start_ping)

    # ── Analytics ping ─────────────────────────────────────────────────────────
    def _ping_server(self):
        import platform as _platform
        try:
            all_folders = [
                f for f in os.listdir(self.mods_dir)
                if os.path.isdir(os.path.join(self.mods_dir, f))
            ]
            paks = self.get_active_paks_path()
            suf = self.get_active_suffix()
            installed = list(self._batch_check_installed(all_folders, paks, suf))
            custom = [f for f in all_folders if f not in installed]

            _worker_post("/ping", {
                "hwid":            _HWID,
                "loader_version":  VERSION,
                "platform":        self.platform_var.get(),
                "installed_mods":  installed,
                "custom_mods":     custom,
                "mod_count":       len(all_folders),
                "os_info":         _platform.version(),
            })
        except Exception:
            pass

    def _start_ping(self):
        threading.Thread(target=self._ping_server, daemon=True).start()

    # ── Config / helpers ──────────────────────────────────────────────────────
    def _load_config(self):
        self._pending_platform = None
        if self.config_file.exists():
            try:
                d = json.loads(self.config_file.read_text(encoding="utf-8"))
                self.custom_game_root = d.get("custom_game_root")
                self.custom_paks_path = d.get("custom_paks_path")
                self.custom_paths     = d.get("custom_paths", [])
                self._rebuild_platforms()
                lp = d.get("last_platform")
                if lp and lp in self.platforms: self._pending_platform = lp
            except Exception: pass

    def _rebuild_platforms(self):
        self.platforms = dict(self._builtin_platforms)
        for cp in self.custom_paths:
            name = cp.get("name", "").strip()
            if name:
                self.platforms[name] = {"suffix": cp.get("suffix", ""), "path": cp.get("path", "")}
        if hasattr(self, "_platform_menu"):
            self._platform_menu.configure(values=list(self.platforms.keys()))
            if self.platform_var.get() not in self.platforms:
                self.platform_var.set(list(self.platforms.keys())[0])
            self._invalidate_paks_cache()

    def _save_config(self):
        try:
            self.config_file.write_text(json.dumps({
                "custom_game_root": self.custom_game_root,
                "custom_paks_path": self.custom_paks_path,
                "custom_paths":     self.custom_paths,
                "last_platform":    self.platform_var.get(),
            }, indent=4), encoding="utf-8")
        except Exception: pass

    def _attempt_auto_detect(self):
        p, msg = _auto_detect_dbd_paks_path()
        if p:
            for name, entry in self._builtin_platforms.items():
                if Path(entry["path"]) == Path(p):
                    self.platform_var.set(name)
                    self._invalidate_paks_cache()
                    self.status_var.set(f"✅ {msg}")
                    return
            existing = next((i for i, cp in enumerate(self.custom_paths)
                             if cp.get("name") == "Auto-detected"), None)
            entry = {"name": "Auto-detected", "path": p, "suffix": "-Windows"}
            if existing is not None:
                self.custom_paths[existing] = entry
            else:
                self.custom_paths.append(entry)
            self._rebuild_platforms()
            self.platform_var.set("Auto-detected")
            self._invalidate_paks_cache()
            self._save_config()
            self.status_var.set(f"✅ {msg}")
        else:
            self.status_var.set(msg)

    def _invalidate_paks_cache(self):
        self._paks_cache_valid = False
        self._paks_cache = None
        self._suffix_cache = None

    def get_active_paks_path(self):
        if self._paks_cache_valid: return self._paks_cache
        plat  = self.platform_var.get()
        raw   = self.platforms.get(plat, {}).get("path", "")
        p     = Path(raw) if raw else None
        result = p if (p and p.exists()) else None
        self._paks_cache = result
        self._paks_cache_valid = True
        return result

    def get_active_suffix(self):
        if self._suffix_cache is not None: return self._suffix_cache
        plat = self.platform_var.get()
        self._suffix_cache = self.platforms.get(plat, {}).get("suffix", "")
        return self._suffix_cache

    def _batch_check_installed(self, folders, paks_path, suffix):
        inst = set()
        if not paks_path or not paks_path.exists(): return inst
        try:
            pf = {f.lower() for f in os.listdir(paks_path)}
        except OSError:
            return inst
        for folder in folders:
            mp = os.path.join(self.mods_dir, folder)
            try:
                for f in os.listdir(mp):
                    if os.path.isfile(os.path.join(mp, f)):
                        b, e = os.path.splitext(f)
                        n = ("-".join(b.split("-")[:-1]) + suffix + e if "-" in b and suffix else b + suffix + e)
                        if n.lower() in pf:
                            inst.add(folder)
                            break
            except OSError:
                continue
        return inst

    # ── Multi‑selection core logic ────────────────────────────────────────────
    def _clear_selection(self):
        self.selected_mods.clear()
        self.last_selected_mod = None
        for name, card in self._card_map.items():
            card.set_selected(False)
        self._update_selection_ui()

    def _select_mods_single(self, folder_name, add=False):
        if add:
            if folder_name in self.selected_mods:
                self.selected_mods.discard(folder_name)
            else:
                self.selected_mods.add(folder_name)
            self.last_selected_mod = folder_name
        else:
            self.selected_mods = {folder_name}
            self.last_selected_mod = folder_name
        for name, card in self._card_map.items():
            card.set_selected(name in self.selected_mods)
        self._update_selection_ui()

    def _select_mods_range(self, folder_name):
        if not self.last_selected_mod or self.last_selected_mod not in self.mod_order:
            self._select_mods_single(folder_name, add=False)
            return
        try:
            idx_last = self.mod_order.index(self.last_selected_mod)
            idx_cur  = self.mod_order.index(folder_name)
        except ValueError:
            self._select_mods_single(folder_name, add=False)
            return
        start, end = sorted([idx_last, idx_cur])
        new_sel = set(self.mod_order[start:end+1])
        self.selected_mods = new_sel
        self.last_selected_mod = folder_name
        for name, card in self._card_map.items():
            card.set_selected(name in self.selected_mods)
        self._update_selection_ui()

    def _on_mod_click(self, folder_name, event):
        ctrl = (event.state & 0x0004) != 0
        shift = (event.state & 0x0001) != 0
        if ctrl:
            self._select_mods_single(folder_name, add=True)
        elif shift:
            self._select_mods_range(folder_name)
        else:
            self._select_mods_single(folder_name, add=False)
            self.select_mod(folder_name)

    def _on_mod_context_menu(self, folder_name, event):
        if folder_name not in self.selected_mods:
            self._select_mods_single(folder_name, add=False)
            self.select_mod(folder_name)

        menu = Menu(self, tearoff=0, bg=BG_PANEL, fg=TEXT_PRI,
                    activebackground=BG_CARD_HOV, activeforeground=ACCENT)
        single = len(self.selected_mods) == 1
        menu.add_command(label="Add to Paks", command=self._bulk_install)
        menu.add_command(label="Remove from Paks", command=self._bulk_uninstall)
        menu.add_command(label="Delete from Loader", command=self._bulk_delete)
        menu.add_separator()
        if single:
            menu.add_command(label="Rename", command=lambda: self.rename_mod(folder_name))
            menu.add_command(label="Open Folder", command=self._open_current_mod_folder)
            menu.add_command(label="Share", command=self._share_current)
        menu.add_separator()
        menu.add_command(label="Clear Selection", command=self._clear_selection)
        menu.post(event.x_root, event.y_root)

    def _update_selection_ui(self):
        cnt = len(self.selected_mods)
        self.status_var.set(f"{cnt} mod{'s' if cnt!=1 else ''} selected" if cnt else "Ready")

        if cnt == 0:
            self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")
            self.detail_frame.place_forget()
            if self._bulk_frame:
                self._bulk_frame.pack_forget()
        elif cnt == 1:
            self.empty_frame.place_forget()
            if self._bulk_frame:
                self._bulk_frame.pack_forget()
            self.detail_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            only_mod = next(iter(self.selected_mods))
            if self.current_mod != only_mod:
                self.select_mod(only_mod)
        else:
            self.empty_frame.place_forget()
            self.detail_frame.place_forget()
            if not self._bulk_frame:
                self._build_bulk_frame()
            else:
                # Ensure it's visible and refresh content
                self._bulk_frame.pack(fill="both", expand=True)
                self._refresh_bulk_table()
            self._bulk_frame.lift()

    def _build_bulk_frame(self):
        """Build a full‑width table showing details of all selected mods."""
        self._bulk_frame = ctk.CTkFrame(self._content, fg_color=BG_ROOT)
        self._bulk_frame.pack(fill="both", expand=True)

        # ----- Top bar with title and action buttons -----
        top_bar = ctk.CTkFrame(self._bulk_frame, fg_color=BG_PANEL, height=52, corner_radius=0)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        self.bulk_title = ctk.CTkLabel(
            top_bar, text="Selected Mods", font=ctk.CTkFont(family="Segoe UI Black", size=16, weight="bold"),
            text_color=ACCENT
        )
        self.bulk_title.pack(side="left", padx=16, pady=10)

        # Action buttons
        btn_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        btn_frame.pack(side="right", padx=12, pady=6)

        ctk.CTkButton(
            btn_frame, text="Install all", width=100, height=34,
            fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
            command=self._bulk_install
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Remove all", width=100, height=34,
            fg_color="#2e1010", hover_color="#4a1515", border_width=1, border_color=RED, text_color=RED,
            command=self._bulk_uninstall
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Delete all", width=100, height=34,
            fg_color=BG_CARD, hover_color=BG_CARD_HOV, text_color=RED,
            command=self._bulk_delete
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Clear selection", width=110, height=34,
            fg_color=BG_FIELD, hover_color=BG_CARD, text_color=TEXT_PRI,
            command=self._clear_selection
        ).pack(side="left", padx=4)

        # ----- Table header -----
        header = ctk.CTkFrame(self._bulk_frame, fg_color=BG_PANEL, height=32, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        col_status = 40
        col_name   = 220
        col_files  = 80
        col_size   = 100
        pad_left = 16

        ctk.CTkLabel(header, text="", width=col_status).pack(side="left", padx=(pad_left, 0))
        ctk.CTkLabel(header, text="MOD NAME", font=_FSM, text_color=TEXT_MUT, width=col_name, anchor="w").pack(side="left")
        ctk.CTkLabel(header, text="FILES", font=_FSM, text_color=TEXT_MUT, width=col_files, anchor="w").pack(side="left")
        ctk.CTkLabel(header, text="SIZE", font=_FSM, text_color=TEXT_MUT, width=col_size, anchor="w").pack(side="left")
        ctk.CTkLabel(header, text="", width=50).pack(side="left", fill="x", expand=True)

        # ----- Scrollable table body -----
        self.bulk_table = ctk.CTkScrollableFrame(
            self._bulk_frame, fg_color="transparent",
            scrollbar_button_color=TEXT_MUT, scrollbar_button_hover_color=TEXT_SEC
        )
        self.bulk_table.pack(fill="both", expand=True, padx=8, pady=8)

        self._refresh_bulk_table()

    def _refresh_bulk_table(self):
        """Clear and rebuild the table rows for the currently selected mods."""
        if not hasattr(self, "bulk_table"):
            return
        # Destroy all existing rows
        for child in self.bulk_table.winfo_children():
            child.destroy()

        if not self.selected_mods:
            return

        # Update title with count
        self.bulk_title.configure(text=f"Selected Mods ({len(self.selected_mods)})")

        # Get current Paks path and suffix once for all checks
        paks_path = self.get_active_paks_path()
        suffix = self.get_active_suffix()

        # For each selected mod, create a row
        for idx, mod_name in enumerate(self.selected_mods):
            mod_folder = os.path.join(self.mods_dir, mod_name)
            if not os.path.isdir(mod_folder):
                continue

            # Compute stats
            file_count, total_size = self._get_mod_stats(mod_name)

            # Check installed status
            installed = self._is_mod_installed_current(mod_name, paks_path, suffix)

            # Row background (alternating)
            bg = BG_CARD if idx % 2 == 0 else BG_FIELD

            row = ctk.CTkFrame(self.bulk_table, fg_color=bg, corner_radius=6, height=36)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            # Status column (green dot if installed)
            dot_color = GREEN if installed else TEXT_MUT
            status_lbl = ctk.CTkLabel(row, text="●", font=_FD, text_color=dot_color, width=40)
            status_lbl.pack(side="left", padx=(16, 0))

            # Mod name
            name_lbl = ctk.CTkLabel(row, text=mod_name, font=_FBM, text_color=TEXT_PRI, width=220, anchor="w")
            name_lbl.pack(side="left")

            # File count
            files_lbl = ctk.CTkLabel(row, text=str(file_count), font=_FBM, text_color=TEXT_PRI, width=80, anchor="w")
            files_lbl.pack(side="left")

            # Total size
            size_lbl = ctk.CTkLabel(row, text=_format_bytes(total_size), font=_FBM, text_color=TEXT_PRI, width=100, anchor="w")
            size_lbl.pack(side="left")

            # Optional extra column (reserved)
            ctk.CTkLabel(row, text="", width=50).pack(side="left", fill="x", expand=True)

    def _get_mod_stats(self, mod_name):
        """Return (file_count, total_size_bytes) for a mod folder."""
        mod_path = os.path.join(self.mods_dir, mod_name)
        if not os.path.isdir(mod_path):
            return 0, 0
        file_count = 0
        total_size = 0
        for f in os.listdir(mod_path):
            fp = os.path.join(mod_path, f)
            if os.path.isfile(fp):
                file_count += 1
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        return file_count, total_size

    def _is_mod_installed_current(self, mod_name, paks_path, suffix):
        """Check if a mod's files are present in the given Paks folder with the given suffix."""
        if not paks_path or not paks_path.exists():
            return False
        mod_path = os.path.join(self.mods_dir, mod_name)
        if not os.path.isdir(mod_path):
            return False
        try:
            paks_files = {f.lower() for f in os.listdir(paks_path)}
        except OSError:
            return False
        for f in os.listdir(mod_path):
            fp = os.path.join(mod_path, f)
            if os.path.isfile(fp):
                base, ext = os.path.splitext(f)
                if "-" in base and suffix:
                    new_name = "-".join(base.split("-")[:-1]) + suffix + ext
                else:
                    new_name = base + suffix + ext
                if new_name.lower() in paks_files:
                    return True
        return False

    # ── Bulk actions ──────────────────────────────────────────────────────────
    def _bulk_install(self):
        if not self.selected_mods:
            return
        if not messagebox.askyesno("Bulk Install",
                f"Install {len(self.selected_mods)} mod(s) into Paks folder?\n"
                "Existing files will be overwritten."):
            return
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return
        suf = self.get_active_suffix()
        success = 0
        for mod_name in list(self.selected_mods):
            mod_path = os.path.join(self.mods_dir, mod_name)
            if not os.path.isdir(mod_path):
                continue
            try:
                for f in os.listdir(mod_path):
                    if os.path.isfile(os.path.join(mod_path, f)):
                        b, e = os.path.splitext(f)
                        nn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
                        shutil.copy2(Path(mod_path) / f, paks / nn)
                success += 1
            except Exception as e:
                messagebox.showerror("Error", f"Failed installing {mod_name}:\n{e}")
        self._invalidate_paks_cache()
        self.status_var.set(f"Installed {success} mod(s)")
        self.load_mods()
        self._clear_selection()

    def _bulk_uninstall(self):
        if not self.selected_mods:
            return
        if not messagebox.askyesno("Bulk Remove",
                f"Remove {len(self.selected_mods)} mod(s) from Paks folder?"):
            return
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return
        suf = self.get_active_suffix()
        removed = 0
        for mod_name in list(self.selected_mods):
            mod_path = os.path.join(self.mods_dir, mod_name)
            if not os.path.isdir(mod_path):
                continue
            try:
                for f in os.listdir(mod_path):
                    if os.path.isfile(os.path.join(mod_path, f)):
                        b, e = os.path.splitext(f)
                        nn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
                        t = paks / nn
                        if t.exists():
                            t.unlink()
                            removed += 1
            except Exception as e:
                messagebox.showerror("Error", f"Failed removing {mod_name}:\n{e}")
        self._invalidate_paks_cache()
        self.status_var.set(f"Removed {removed} file(s)")
        self.load_mods()
        self._clear_selection()

    def _bulk_delete(self):
        if not self.selected_mods:
            return
        if not messagebox.askyesno("Bulk Delete",
                f"Permanently DELETE {len(self.selected_mods)} mod(s) from the loader?\n"
                "This will not affect files already in your Paks folder."):
            return
        for mod_name in list(self.selected_mods):
            mod_path = os.path.join(self.mods_dir, mod_name)
            if os.path.isdir(mod_path):
                try:
                    shutil.rmtree(mod_path)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to delete {mod_name}:\n{e}")
        self.load_mods()
        self._clear_selection()

    # ── UI building ───────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_statusbar()
        self._build_sidebar()
        self._build_main()

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=26, fg_color=BG_PANEL, corner_radius=0)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(bar, textvariable=self.status_var, font=_FST, text_color=TEXT_SEC).pack(side="left", padx=16, pady=4)
        ctk.CTkLabel(bar, text=f"v{VERSION}", font=_FST, text_color=TEXT_MUT).pack(side="right", padx=16, pady=4)
        self._update_btn = ctk.CTkButton(bar, text="", height=20, font=_FST,
                                         fg_color="#1a2e1a", hover_color="#223d22",
                                         border_width=1, border_color=GREEN, text_color=GREEN,
                                         corner_radius=6, command=self._do_update)

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=290, fg_color=BG_PANEL, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=40, pady=(20,0))
        ctk.CTkLabel(logo, text="PAK", font=_FL, text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(logo, text=" Loader", font=_FLS, text_color=TEXT_PRI).pack(side="left", pady=(1,0))
        ctk.CTkFrame(sb, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=16, pady=(12,0))
        ctk.CTkLabel(sb, text="MODS", font=_FSM, text_color=TEXT_MUT).pack(anchor="w", padx=20, pady=(10,3))
        self.search_var = ctk.StringVar()
        se = ctk.CTkEntry(sb, textvariable=self.search_var, placeholder_text="Search mods...",
                          height=34, fg_color=BG_FIELD, text_color=TEXT_PRI,
                          border_color=TEXT_MUT, corner_radius=9, font=_FBM)
        se.pack(fill="x", padx=16, pady=(0,10))
        se.bind("<KeyRelease>", self._on_search_key)
        self.mods_scroll = ctk.CTkScrollableFrame(sb, fg_color="transparent",
                                                  scrollbar_button_color=TEXT_MUT,
                                                  scrollbar_button_hover_color=TEXT_SEC)
        self.mods_scroll.pack(fill="both", expand=True, padx=10, pady=(0,4))
        ctk.CTkFrame(sb, height=1, fg_color=TEXT_MUT).pack(fill="x", padx=16)
        btns = ctk.CTkFrame(sb, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(btns, text="🌐  Browse Mods", height=40, font=_FBB,
                      fg_color=BG_CARD, hover_color=BG_CARD_HOV, text_color=ACCENT,
                      border_width=1, border_color=ACCENT_DIM, corner_radius=9,
                      command=self.open_mod_browser).pack(fill="x", pady=(0,6))
        ctk.CTkButton(btns, text="＋  Import Mod", height=40, font=_FBB,
                      fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
                      corner_radius=9, command=self.import_mod_folder).pack(fill="x", pady=(0,6))
        ctk.CTkButton(btns, text="↺  Refresh", height=32, font=_FBM,
                      fg_color=BG_CARD, hover_color=BG_CARD_HOV, text_color=TEXT_PRI,
                      corner_radius=9, command=self.load_mods).pack(fill="x")

    def _build_main(self):
        self.main_area = ctk.CTkFrame(self, fg_color=BG_ROOT)
        self.main_area.pack(side="left", fill="both", expand=True)
        topbar = ctk.CTkFrame(self.main_area, fg_color=BG_PANEL, height=58, corner_radius=0)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        pf = ctk.CTkFrame(topbar, fg_color="transparent")
        pf.pack(side="left", padx=24, pady=8)
        ctk.CTkLabel(pf, text="PLATFORM", font=_FSM, text_color=TEXT_MUT).pack(anchor="w")
        dp = self._pending_platform or "Microsoft Store (-WinGDK)"
        self.platform_var = ctk.StringVar(value=dp)
        self._platform_menu = ctk.CTkOptionMenu(
                          pf, values=list(self.platforms.keys()), variable=self.platform_var,
                          command=self.on_platform_change, width=290, height=30,
                          fg_color=BG_FIELD, button_color=ACCENT_DIM, button_hover_color=ACCENT,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOV,
                          font=_FBM, text_color=TEXT_PRI, corner_radius=7)
        self._platform_menu.pack(anchor="w")
        ctk.CTkButton(topbar, text="⚙  Path Editor", height=34, width=130,
                      fg_color=BG_FIELD, hover_color=BG_CARD, border_width=1,
                      border_color=ACCENT_DIM, text_color=ACCENT, corner_radius=9,
                      command=self.open_path_editor).pack(side="right", padx=(0,6), pady=12)
        bg = ctk.CTkFrame(topbar, fg_color="transparent")
        bg.pack(side="right", padx=20, pady=12)
        ctk.CTkButton(bg, text="🧹  Clean DBD", height=34, width=130, fg_color="#2e1010",
                      hover_color="#4a1515", border_width=1, border_color=RED, text_color=RED,
                      corner_radius=9, command=self.clean_paks_folder).pack(side="left", padx=(0,10))
        ctk.CTkButton(bg, text="📂  Open Paks Folder", height=34, width=180,
                      fg_color=BG_FIELD, hover_color=BG_CARD, border_width=1,
                      border_color=TEXT_MUT, text_color=TEXT_PRI, corner_radius=9,
                      command=self.open_paks_folder).pack(side="left")
        content = ctk.CTkFrame(self.main_area, fg_color="transparent")
        content.pack(fill="both", expand=True)
        self._content = content
        self.empty_frame = ctk.CTkFrame(content, fg_color="transparent")
        self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self.empty_frame, text="📦", font=ctk.CTkFont(size=60)).pack()
        ctk.CTkLabel(self.empty_frame, text="Select a mod to get started",
                     font=_FT, text_color=TEXT_PRI).pack(pady=(10,5))
        hint = ("Drag & drop archives or folders here to import"
                if _HAS_DND else "Import a mod folder or archive using the button below.")
        ctk.CTkLabel(self.empty_frame, text=hint, font=_FBM, text_color=TEXT_SEC, wraplength=380).pack()
        self.detail_frame = ctk.CTkFrame(content, fg_color="transparent")
        tr = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        tr.pack(fill="x", padx=30, pady=(24,0))
        self.mod_title = ctk.CTkLabel(tr, text="", font=_FT, text_color=TEXT_PRI, anchor="w")
        self.mod_title.pack(side="left")
        self.mod_status_badge = ctk.CTkLabel(tr, text="", font=_FB, fg_color=BG_CARD, corner_radius=7, padx=10, pady=3)
        self.mod_status_badge.pack(side="left", padx=(14,0), pady=(3,0))
        self.mod_stats_badge = ctk.CTkLabel(tr, text="", font=_FB, fg_color=BG_CARD, corner_radius=7, padx=10, pady=3, text_color=TEXT_SEC)
        self.mod_stats_badge.pack(side="left", padx=(10,0), pady=(3,0))
        self.conflict_label = ctk.CTkLabel(tr, text="", font=_FB, text_color=ORANGE)
        self.conflict_label.pack(side="left", padx=(10,0), pady=(3,0))
        for txt, bc, fc, hc, cmd, w, px in [
            ("📁 Open Folder", TEXT_MUT, "transparent", BG_CARD_HOV, self._open_current_mod_folder, 110, (16,0)),
            ("✏ Rename", ACCENT_DIM, "transparent", "#2a2040", self._rename_current, 90, (8,0)),
            ("🗑 Delete", RED, "transparent", "#3a1a1a", self._delete_current, 80, (8,0)),
            ("🔗 Share", GREEN, "transparent", "#1a2a1a", self._share_current, 80, (8,0)),
        ]:
            ctk.CTkButton(tr, text=txt, width=w, height=28, fg_color=fc, hover_color=hc,
                          border_width=1, border_color=bc,
                          text_color=TEXT_PRI if bc == TEXT_MUT else bc,
                          corner_radius=7, command=cmd).pack(side="left", padx=(px[0], px[1]), pady=(3,0))
        ctk.CTkLabel(self.detail_frame, text="Files in mod", font=_FSM, text_color=TEXT_MUT).pack(anchor="w", padx=30, pady=(18,5))
        fo = ctk.CTkFrame(self.detail_frame, fg_color=BG_FIELD, corner_radius=12)
        fo.pack(fill="both", expand=True, padx=30)
        self.files_container = ctk.CTkScrollableFrame(fo, fg_color="transparent", scrollbar_button_color=TEXT_MUT)
        self.files_container.pack(fill="both", expand=True, padx=4, pady=4)
        br = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        br.pack(fill="x", padx=30, pady=20)
        self.add_btn = ctk.CTkButton(br, text="ADD TO PAKS", height=50, font=_FBL,
                                     fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black",
                                     corner_radius=12, command=self.install_mod)
        self.add_btn.pack(side="left", fill="x", expand=True, padx=(0,8))
        self.remove_btn = ctk.CTkButton(br, text="REMOVE FROM PAKS", height=50, font=_FBL,
                                        fg_color="#2e1010", hover_color="#4a1515", border_width=1,
                                        border_color=RED, text_color=RED, corner_radius=12,
                                        command=self.uninstall_mod, state="disabled")
        self.remove_btn.pack(side="left", fill="x", expand=True, padx=(8,0))

    def _setup_drag_feedback(self):
        self._orig_bg = self.main_area.cget("fg_color")
        self.main_area.bind("<Enter>", lambda e: self.main_area.configure(fg_color="#1a1a2e"))
        self.main_area.bind("<Leave>", lambda e: self.main_area.configure(fg_color=self._orig_bg))

    def _on_search_key(self, _=None):
        if self._search_after_id: self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(180, self.load_mods)

    def load_mods(self):
        self._clear_selection()
        for w in self.mods_scroll.winfo_children(): w.destroy()
        self._card_map.clear()
        self.mod_order.clear()

        q = self.search_var.get().strip().lower()
        folders = [f for f in os.listdir(self.mods_dir) if os.path.isdir(os.path.join(self.mods_dir, f))]
        if q: folders = [f for f in folders if q in f.lower()]
        if not folders:
            ctk.CTkLabel(self.mods_scroll, text="No mods found.", font=_FBM,
                         text_color=TEXT_SEC, justify="center").pack(pady=40)
            return

        paks = self.get_active_paks_path()
        suf = self.get_active_suffix()
        inst = self._batch_check_installed(folders, paks, suf)
        folders.sort(key=lambda f: (0 if f in inst else 1, f.lower()))
        self.mod_order = folders[:]

        for folder in folders:
            card = ModCard(self.mods_scroll, folder,
                           on_select=self._on_mod_click,
                           on_rename=self.rename_mod,
                           on_context_menu=self._on_mod_context_menu)
            card.pack(fill="x", pady=2, padx=2)
            self._card_map[folder] = card
            card.set_installed(folder in inst)

        if self.current_mod and self.current_mod in self._card_map:
            self._card_map[self.current_mod].set_selected(True)
            self.selected_mods = {self.current_mod}
            self._update_selection_ui()

    def select_mod(self, folder_name):
        self.current_mod = folder_name
        self.current_mod_path = os.path.join(self.mods_dir, folder_name)
        self.empty_frame.place_forget()
        if self._bulk_frame:
            self._bulk_frame.pack_forget()
        self.detail_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.mod_title.configure(text=folder_name)
        inst = self.is_mod_installed()
        if inst:
            self.mod_status_badge.configure(text="● Installed", text_color=GREEN, fg_color="#0d2a1a")
            self.add_btn.configure(text="RE-ADD TO PAKS", fg_color=ORANGE, hover_color="#c2410c", text_color="black")
            self.remove_btn.configure(state="normal")
        else:
            self.mod_status_badge.configure(text="● Not Installed", text_color=ORANGE, fg_color="#2a1a0d")
            self.add_btn.configure(text="ADD TO PAKS", fg_color=ACCENT, hover_color=ACCENT_DIM, text_color="black")
            self.remove_btn.configure(state="disabled")
        fc, ts = self._compute_mod_stats()
        self.mod_stats_badge.configure(text=f"{fc} file(s) • {_format_bytes(ts)}")
        conflicts = self._check_conflicts()
        self.conflict_label.configure(
            text=(f"⚠ Conflicts with: {', '.join(conflicts[:3])}"
                  + ("..." if len(conflicts) > 3 else "")) if conflicts else "")
        if folder_name in self._card_map:
            self._card_map[folder_name].set_installed(inst)
        self.show_final_files()

    def _compute_mod_stats(self):
        if not self.current_mod_path: return (0, 0)
        tf = ts = 0
        for f in os.listdir(self.current_mod_path):
            p = os.path.join(self.current_mod_path, f)
            if os.path.isfile(p):
                tf += 1
                try:
                    ts += os.path.getsize(p)
                except Exception:
                    pass
        return (tf, ts)

    def _check_conflicts(self):
        if not self.current_mod_path: return []
        suf = self.get_active_suffix()
        tgt = set()
        for f in os.listdir(self.current_mod_path):
            if os.path.isfile(os.path.join(self.current_mod_path, f)):
                b, e = os.path.splitext(f)
                tgt.add(("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e).lower())
        out = []
        for mf in os.listdir(self.mods_dir):
            if mf == self.current_mod: continue
            op = os.path.join(self.mods_dir, mf)
            if not os.path.isdir(op): continue
            for f in os.listdir(op):
                if os.path.isfile(os.path.join(op, f)):
                    b, e = os.path.splitext(f)
                    n = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e).lower()
                    if n in tgt:
                        out.append(mf)
                        break
        return out

    def is_mod_installed(self):
        if not self.current_mod: return False
        paks = self.get_active_paks_path()
        if not paks: return False
        return self.current_mod in self._batch_check_installed([self.current_mod], paks, self.get_active_suffix())

    def on_platform_change(self, _):
        self._invalidate_paks_cache()
        self._save_config()
        paks = self.get_active_paks_path()
        suf = self.get_active_suffix()
        inst = self._batch_check_installed(list(self._card_map), paks, suf)
        for f, c in self._card_map.items():
            c.set_installed(f in inst)
        if self.current_mod:
            self.select_mod(self.current_mod)

    def _open_current_mod_folder(self):
        if self.current_mod_path and os.path.exists(self.current_mod_path):
            subprocess.Popen(f'explorer "{self.current_mod_path}"')
        else:
            messagebox.showerror("Error", "Mod folder not found.")

    def _rename_current(self):
        if self.current_mod: self.rename_mod(self.current_mod)

    def _delete_current(self):
        if self.current_mod: self.delete_mod(self.current_mod)

    def _share_current(self):
        if self.current_mod and self.current_mod_path:
            ShareDialog(self, self.current_mod, self.current_mod_path)

    def open_mod_browser(self):
        if hasattr(self, "_browser_panel") and self._browser_panel.winfo_exists():
            return
        self.empty_frame.place_forget()
        self.detail_frame.place_forget()
        if self._bulk_frame:
            self._bulk_frame.pack_forget()
        self._browser_panel = ModBrowserPanel(
            self._content,
            mods_dir=self.mods_dir,
            on_install_cb=self._on_browser_install,
            on_close_cb=self._close_mod_browser,
        )
        self._browser_panel.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _close_mod_browser(self):
        if hasattr(self, "_browser_panel") and self._browser_panel.winfo_exists():
            self._browser_panel.destroy()
        self.load_mods()
        if self.current_mod and os.path.isdir(os.path.join(self.mods_dir, self.current_mod)):
            self.select_mod(self.current_mod)
        else:
            self.current_mod = None
            self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")

    def _on_browser_install(self, mod_name):
        self.load_mods()
        self.status_var.set(f"✅ Installed from browser: {mod_name}")

    def rename_mod(self, folder_name):
        nn = simpledialog.askstring("Rename Mod", f"New name for '{folder_name}':",
                                    initialvalue=folder_name, parent=self)
        if not nn or nn.strip() == folder_name: return
        nn = nn.strip()
        if any(c in nn for c in r'\/:*?"<>|'):
            messagebox.showerror("Invalid Name", "Name contains invalid characters.")
            return
        op = os.path.join(self.mods_dir, folder_name)
        np = os.path.join(self.mods_dir, nn)
        if os.path.exists(np):
            messagebox.showerror("Name Taken", f"A mod named '{nn}' already exists.")
            return
        try:
            os.rename(op, np)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if folder_name in self._card_map:
            card = self._card_map.pop(folder_name)
            card.update_name(nn)
            self._card_map[nn] = card
        if self.current_mod == folder_name:
            self.current_mod = nn
            self.current_mod_path = np
            self.mod_title.configure(text=nn)
        if folder_name in self.selected_mods:
            self.selected_mods.discard(folder_name)
            self.selected_mods.add(nn)
        if self.last_selected_mod == folder_name:
            self.last_selected_mod = nn
        if folder_name in self.mod_order:
            idx = self.mod_order.index(folder_name)
            self.mod_order[idx] = nn
        self.status_var.set(f"Renamed: {folder_name} → {nn}")
        self.load_mods()

    def delete_mod(self, folder_name):
        if not messagebox.askyesno("Delete Mod",
                f"Remove '{folder_name}' from the loader?\n\nFiles in Paks folder will NOT be removed."):
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
        for w in self.files_container.winfo_children(): w.destroy()
        files = [f for f in os.listdir(self.current_mod_path)
                 if os.path.isfile(os.path.join(self.current_mod_path, f))]
        suf = self.get_active_suffix()
        if not files:
            ctk.CTkLabel(self.files_container, text="No files found in this mod folder.",
                         font=_FBM, text_color=TEXT_SEC).pack(padx=20, pady=16)
            return
        for i, f in enumerate(files):
            b, e = os.path.splitext(f)
            nn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
            row = ctk.CTkFrame(self.files_container,
                               fg_color="#16162a" if i % 2 == 0 else "transparent", corner_radius=7)
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(row, text="→", font=_FM, text_color=ACCENT, width=22).pack(side="left", padx=(10,5), pady=7)
            ctk.CTkLabel(row, text=nn, font=_FM, text_color="#b8b8ff", anchor="w").pack(side="left", fill="x", expand=True, pady=7)

    def open_path_editor(self):
        if hasattr(self, "_path_panel") and self._path_panel.winfo_exists():
            return
        self.empty_frame.place_forget()
        self.detail_frame.place_forget()
        if hasattr(self, "_browser_panel") and self._browser_panel.winfo_exists():
            self._browser_panel.place_forget()
        if self._bulk_frame:
            self._bulk_frame.pack_forget()
        self._path_panel = PathEditorPanel(
            self._content,
            custom_paths=self.custom_paths,
            builtin_platforms=self._builtin_platforms,
            on_save=self._on_paths_saved,
            on_close=self._close_path_editor,
        )
        self._path_panel.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _close_path_editor(self):
        if hasattr(self, "_path_panel") and self._path_panel.winfo_exists():
            self._path_panel.destroy()
        if self.current_mod and os.path.isdir(os.path.join(self.mods_dir, self.current_mod)):
            self.select_mod(self.current_mod)
        else:
            self.empty_frame.place(relx=0.5, rely=0.5, anchor="center")

    def _on_paths_saved(self, new_paths):
        self.custom_paths = new_paths
        self._rebuild_platforms()
        self._save_config()
        self._close_path_editor()
        self.load_mods()
        if self.current_mod: self.select_mod(self.current_mod)
        self.status_var.set(f"Paths saved  ({len(new_paths)} custom)")

    def clean_paks_folder(self):
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Not Found", "Paks folder not found.")
            return
        if not messagebox.askyesno("Clean DBD Paks",
                "This will remove ALL mod files from your Paks folder.\nContinue?"):
            return
        suf = self.get_active_suffix()
        removed = 0
        for mf in os.listdir(self.mods_dir):
            mp = Path(self.mods_dir) / mf
            if not mp.is_dir(): continue
            for f in os.listdir(mp):
                if os.path.isfile(mp / f):
                    b, e = os.path.splitext(f)
                    fn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
                    t = paks / fn
                    if t.exists():
                        try:
                            t.unlink()
                            removed += 1
                        except Exception:
                            pass
        self._invalidate_paks_cache()
        self.status_var.set(f"🧹 Cleaned: removed {removed} file(s)")
        self.load_mods()
        if self.current_mod: self.select_mod(self.current_mod)

    def open_paks_folder(self):
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Not Found", "Paks folder not found.")
            return
        try:
            subprocess.Popen(f'explorer "{paks}"')
            self.status_var.set(f"Opened: {paks}")
        except Exception:
            messagebox.showerror("Error", "Could not open folder.")

    def import_mod_folder(self):
        all_exts = "*.zip *.ZIP"
        if _HAS_RAR: all_exts += " *.rar *.RAR *.cbr"
        if _HAS_7Z: all_exts += " *.7z *.7Z"
        types = [("Mod archives", all_exts), ("ZIP archive", "*.zip *.ZIP")]
        if _HAS_RAR: types.append(("RAR archive", "*.rar *.RAR *.cbr"))
        if _HAS_7Z: types.append(("7-Zip archive", "*.7z *.7Z"))
        types.append(("All files", "*.*"))
        paths = filedialog.askopenfilenames(title="Select Mod Archive(s)", filetypes=types)
        if not paths:
            folder = filedialog.askdirectory(title="Or select a Mod Folder")
            if folder: paths = (folder,)
        if not paths: return
        for path in paths:
            try:
                self._do_import(path, silent=len(paths) > 1)
            except Exception as e:
                messagebox.showerror("Import Error", f"{Path(path).name}:\n{e}")
        self.load_mods()

    def _do_import(self, path, silent=False):
        p = Path(path)
        if p.is_dir():
            name = p.name.strip()
            dest = os.path.join(self.mods_dir, name)
            if os.path.exists(dest):
                if not messagebox.askyesno("Overwrite", f"Mod '{name}' already exists.\nOverwrite?"): return
                shutil.rmtree(dest)
            shutil.copytree(str(p), dest)
            if not silent: messagebox.showinfo("Imported", f"Mod '{name}' imported successfully.")
            self.status_var.set(f"Imported: {name}")
            if not silent: self.load_mods()
            return
        suf = p.suffix.lower()
        if suf not in {".zip", ".rar", ".cbr", ".7z"}:
            raise RuntimeError(f"Unsupported format: {suf}")
        name = p.stem
        dest = os.path.join(self.mods_dir, name)
        if os.path.exists(dest):
            if not messagebox.askyesno("Overwrite", f"Mod '{name}' already exists.\nOverwrite?"): return
            shutil.rmtree(dest)
        self.status_var.set(f"Extracting {p.name}...")
        self.update_idletasks()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                _extract_archive(path, tmp, suf)
                entries = [e for e in os.listdir(tmp) if not e.startswith("__MACOSX")]
                er = (os.path.join(tmp, entries[0]) if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])) else tmp)
                shutil.copytree(er, dest, dirs_exist_ok=True)
            if not silent: messagebox.showinfo("Imported", f"Mod '{name}' imported successfully.")
            self.status_var.set(f"Imported: {name}")
            if not silent: self.load_mods()
        except Exception:
            if os.path.exists(dest): shutil.rmtree(dest, ignore_errors=True)
            raise

    def install_mod(self):
        if not self.current_mod: return
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return
        suf = self.get_active_suffix()
        conflicts = self._check_conflicts()
        if conflicts and not messagebox.askyesno("Conflict Detected",
                f"This mod conflicts with:\n{', '.join(conflicts)}\n\nContinue anyway?"):
            return
        self.add_btn.configure(text="ADDING…", state="disabled")
        self.status_var.set(f"Adding {self.current_mod}…")
        self.update_idletasks()
        try:
            for f in os.listdir(self.current_mod_path):
                if os.path.isfile(os.path.join(self.current_mod_path, f)):
                    b, e = os.path.splitext(f)
                    nn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
                    shutil.copy2(Path(self.current_mod_path) / f, paks / nn)
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
        if not self.current_mod: return
        if not messagebox.askyesno("Confirm Remove", f"Remove '{self.current_mod}' from Paks folder?"):
            return
        paks = self.get_active_paks_path()
        if not paks:
            messagebox.showerror("Path Error", "Paks folder not found.")
            return
        suf = self.get_active_suffix()
        self.remove_btn.configure(state="disabled")
        self.status_var.set(f"Removing {self.current_mod}…")
        self.update_idletasks()
        try:
            removed = 0
            for f in os.listdir(self.current_mod_path):
                if os.path.isfile(os.path.join(self.current_mod_path, f)):
                    b, e = os.path.splitext(f)
                    nn = ("-".join(b.split("-")[:-1]) + suf + e if "-" in b and suf else b + suf + e)
                    t = paks / nn
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
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection("raw.githubusercontent.com", timeout=8, context=ctx)
            conn.request("GET", "/NixxGame/DBDPakLoader/main/version.txt",
                         headers={"User-Agent":"DBDPakLoader/1.0"})
            resp = conn.getresponse()
            latest = resp.read().decode().strip().lstrip("vV")
            conn.close()
            if latest and latest != VERSION:
                self._latest_version = latest
                self.after(0, self._show_update_badge)
        except Exception:
            pass

    def _show_update_badge(self):
        self._update_btn.configure(text=f"⬆  v{self._latest_version} available — click to update")
        self._update_btn.pack(side="right", padx=(0,10), pady=3)

    def _do_update(self):
        if not messagebox.askyesno("Update PAK Loader",
                f"Update from v{VERSION} → v{self._latest_version}?\n\n"
                "The app will restart automatically after updating."):
            return
        self._update_btn.configure(text="⬇  Downloading…", state="disabled")
        self.update_idletasks()
        def _run():
            try:
                ctx = ssl.create_default_context()
                for fn in ["loader.py", "requirements.txt", "version.txt"]:
                    conn = http.client.HTTPSConnection("raw.githubusercontent.com", timeout=30, context=ctx)
                    conn.request("GET", f"/NixxGame/DBDPakLoader/main/{fn}",
                                 headers={"User-Agent":"DBDPakLoader/1.0"})
                    resp = conn.getresponse()
                    if resp.status == 200:
                        dest = _SCRIPT_DIR / fn
                        tmp = dest.with_suffix(dest.suffix + ".new")
                        tmp.write_bytes(resp.read())
                        tmp.replace(dest)
                    conn.close()
                self.after(0, self._restart_after_update)
            except Exception as e:
                self.after(0, lambda: (
                    messagebox.showerror("Update Failed", str(e)),
                    self._update_btn.configure(text="⬆  Update available — click to install", state="normal")))
        threading.Thread(target=_run, daemon=True).start()

    def _restart_after_update(self):
        messagebox.showinfo("Update Complete", "Files updated successfully!\nThe app will now restart.")
        bat = _SCRIPT_DIR / "run.bat"
        if bat.exists():
            subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(_SCRIPT_DIR),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(["python", str(_SCRIPT_DIR / "loader.py")], cwd=str(_SCRIPT_DIR))
        self.destroy()

    def _on_drop(self, event):
        self.main_area.configure(fg_color=self._orig_bg)
        raw = event.data.strip()
        import re
        paths = ([a or b for a, b in re.findall(r'\{([^}]+)\}|(\S+)', raw)]
                 if raw.startswith("{") else raw.split())
        imported = 0
        for p in paths:
            p = p.strip().strip("{}")
            if p:
                try:
                    self._do_import(p, silent=len(paths) > 1)
                    imported += 1
                except Exception as e:
                    messagebox.showerror("Import Error", str(e))
        if len(paths) > 1:
            self.load_mods()
            self.status_var.set(f"Imported {imported} of {len(paths)} items")

if __name__ == "__main__":
    app = DBDModLoader()
    app.mainloop()
