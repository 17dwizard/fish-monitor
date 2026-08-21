"""
Fish Monitor — 摸鱼看盘小工具
透明背景 + 纯黑文字 + 只显示百分比 + 系统托盘
数据源: Sina/腾讯(免费) + Longbridge SDK(实时夜盘)
"""

import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, messagebox

# ── Constants ──────────────────────────────────────────────────────
APP_NAME = "Fish Monitor"
VERSION = "1.0.0"
CONFIG_FILE = Path(__file__).parent / "config.json"
TRANSPARENT_KEY = "#f0f0f0"  # Windows transparent color key
REFRESH_INTERVAL = 10  # seconds

# ── Default Config ─────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "stocks": [
        {"code": "s_sh000001", "name": "上证指数"}
    ],
    "opacity": 0.88,
    "position": None,  # {x, y} or None = center
    "font_size": 11,
    "refresh_interval": 10,
}


def load_config():
    """Load config from JSON file, create default if missing."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Merge with defaults for missing keys
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except Exception:
            pass
    # Create default config
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """Save config to JSON file."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Data Sources (Hybrid: Sina for A/US, Tencent for HK) ──────────
import urllib.request


def _sina_fetch(codes: list[str]) -> dict:
    """Fetch A-share & US quotes from Sina Finance."""
    if not codes:
        return {}
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[Sina Error] {e}", file=sys.stderr)
        return {}

    results = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'var hq_str_(.*?)="(.*)";', line)
        if not match:
            continue
        code = match.group(1)
        values = match.group(2).split(",")
        if not values or not values[0]:
            continue
        try:
            extra = {}
            if code.startswith("s_sh") or code.startswith("s_sz"):
                name = values[0]
                price = float(values[1])
                change_pct = float(values[3])
            elif code.startswith("gb_"):
                name = values[0]
                price = float(values[1])
                change_pct = float(values[2])
                # Pre/post market data (only for US stocks)
                # [21]=after-hours price, [22]=AH change, [23]=AH %, [24]=AH time
                # [26]=pre-market price, [35]=prev close
                try:
                    ah_price = float(values[21]) if len(values) > 21 and values[21] and float(values[21]) > 0 else None
                    ah_pct = float(values[23]) if len(values) > 23 and values[23] else None
                    pm_price = float(values[26]) if len(values) > 26 and values[26] and float(values[26]) > 0 else None
                    prev_close = float(values[35]) if len(values) > 35 and values[35] and float(values[35]) > 0 else None
                    if ah_price and ah_pct is not None:
                        extra["ah_price"] = ah_price
                        extra["ah_pct"] = ah_pct
                        extra["ah_time"] = values[24] if len(values) > 24 else ""
                    if pm_price and prev_close:
                        extra["pm_price"] = pm_price
                        extra["pm_pct"] = (pm_price - prev_close) / prev_close * 100
                except (ValueError, IndexError):
                    pass
            else:
                continue
            entry = {"name": name, "price": price, "change_pct": change_pct}
            if extra:
                entry["extra"] = extra
            results[code] = entry
        except (ValueError, IndexError) as e:
            print(f"[Sina Parse] {code}: {e}", file=sys.stderr)
    return results


def _tencent_fetch_hk(codes: list[str]) -> dict:
    """
    Fetch HK quotes from Tencent Finance (faster than Sina for HK).
    Input codes: Sina-style 'hkXXXXX' → converts to Tencent 'r_hkXXXXX'
    """
    if not codes:
        return {}
    # Convert: hk01810 → r_hk01810
    tc_codes = [f"r_{c}" for c in codes]
    url = f"https://qt.gtimg.cn/q={','.join(tc_codes)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.qq.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[Tencent Error] {e}", file=sys.stderr)
        return {}

    results = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or "~" not in line:
            continue
        match = re.match(r'v_r_(.*?)="(.*)";', line)
        if not match:
            continue
        code = match.group(1)  # e.g. "hk01810"
        fields = match.group(2).split("~")
        try:
            name = fields[1]           # Chinese name
            price = float(fields[3])   # current price
            prev_close = float(fields[4])  # previous close
            change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else \
                (price - prev_close) / prev_close * 100 if prev_close else 0
            results[code] = {"name": name, "price": price, "change_pct": change_pct}
        except (ValueError, IndexError) as e:
            print(f"[Tencent Parse] {code}: {e}", file=sys.stderr)
    return results


def fetch_quotes(codes: list[str]) -> dict:
    """
    Hybrid fetch: Longport for US (real-time overnight), Sina for A-share, Tencent for HK.
    Returns: {code: {"name": str, "price": float, "change_pct": float, "extra": {...}}}
    """
    sina_codes = [c for c in codes if c.startswith("s_sh") or c.startswith("s_sz")]
    hk_codes = [c for c in codes if c.startswith("hk")]
    us_codes = [c for c in codes if c.startswith("gb_")]

    # Fetch in parallel
    results = {}
    threads = []

    if sina_codes:
        def sina_w():
            results.update(_sina_fetch(sina_codes))
        threads.append(threading.Thread(target=sina_w, daemon=True))

    if hk_codes:
        def tencent_w():
            results.update(_tencent_fetch_hk(hk_codes))
        threads.append(threading.Thread(target=tencent_w, daemon=True))

    if us_codes:
        def us_w():
            lp = _longport_fetch_us(us_codes)
            if lp:
                results.update(lp)
            else:
                # Fallback to Sina if longport unavailable
                results.update(_sina_fetch(us_codes))
        threads.append(threading.Thread(target=us_w, daemon=True))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    return results


# ── Longbridge SDK (real-time overnight/pre/post for US stocks) ───
_longport_ctx = None


def _get_longport_ctx():
    """Get or create Longport QuoteContext (lazy init)."""
    global _longport_ctx
    if _longport_ctx is not None:
        return _longport_ctx

    cfg = load_config()
    creds = cfg.get("longport", {})
    app_key = creds.get("app_key", "")
    app_secret = creds.get("app_secret", "")
    access_token = creds.get("access_token", "")

    if not all([app_key, app_secret, access_token]):
        return None

    try:
        from longport.openapi import Config, QuoteContext
        config = Config(
            app_key=app_key,
            app_secret=app_secret,
            access_token=access_token,
        )
        _longport_ctx = QuoteContext(config)
        return _longport_ctx
    except Exception as e:
        print(f"[Longport Error] {e}", file=sys.stderr)
        return None


def _longport_fetch_us(codes: list[str]) -> dict:
    """
    Fetch US stock quotes via Longbridge SDK with overnight/pre/post market data.
    Input codes: Sina-style 'gb_xxxx' → converts to 'XXXX.US'
    """
    ctx = _get_longport_ctx()
    if ctx is None:
        return {}

    # Convert: gb_skdd → SKDD.US, gb_$dji → $DJI.US
    symbols = []
    code_map = {}
    for c in codes:
        ticker = c.replace("gb_", "").upper()
        symbol = f"{ticker}.US"
        symbols.append(symbol)
        code_map[symbol] = c

    try:
        from longport.openapi import QuoteType
        quotes = ctx.quote(symbols)
    except Exception as e:
        print(f"[Longport Quote Error] {e}", file=sys.stderr)
        return {}

    results = {}
    for q in quotes:
        code = code_map.get(str(q.symbol), str(q.symbol))
        try:
            price = float(q.last_done)
            prev = float(q.prev_close)
            change_pct = (price - prev) / prev * 100 if prev else 0

            entry = {
                "name": str(q.symbol),  # Will be updated by Sina if available
                "price": price,
                "change_pct": change_pct,
            }

            # Overnight / pre / post market
            extra = {}
            if hasattr(q, "overnight") and q.overnight:
                oh = q.overnight
                oh_price = float(oh.last_done) if hasattr(oh, "last_done") else float(oh.price)
                oh_prev = float(oh.prev_close)
                extra["ah_price"] = oh_price
                extra["ah_pct"] = (oh_price - oh_prev) / oh_prev * 100 if oh_prev else 0
                extra["ah_time"] = "overnight"
            if hasattr(q, "pre_market") and q.pre_market:
                pm = q.pre_market
                pm_price = float(pm.last_done) if hasattr(pm, "last_done") else float(pm.price)
                pm_prev = float(pm.prev_close)
                extra["pm_price"] = pm_price
                extra["pm_pct"] = (pm_price - pm_prev) / pm_prev * 100 if pm_prev else 0
            if hasattr(q, "post_market") and q.post_market:
                pt = q.post_market
                pt_price = float(pt.last_done) if hasattr(pt, "last_done") else float(pt.price)
                pt_prev = float(pt.prev_close)
                extra["ah_price"] = pt_price
                extra["ah_pct"] = (pt_price - pt_prev) / pt_prev * 100 if pt_prev else 0
                extra["ah_time"] = "post_market"

            if extra:
                entry["extra"] = extra
            results[code] = entry
        except Exception as e:
            print(f"[Longport Parse] {code}: {e}", file=sys.stderr)

    return results


def format_pct(pct: float) -> str:
    """Format percentage with sign."""
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


# ── Main Application ──────────────────────────────────────────────
class FishMonitor:
    def __init__(self):
        self.config = load_config()
        self.running = True
        self.visible = True

        # Build UI
        self._build_root()
        self._build_labels()
        self._build_tray()

        # Drag state
        self._drag_x = 0
        self._drag_y = 0

        # Start data fetch thread
        self._fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

    def _build_root(self):
        """Create the main transparent window."""
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)  # No title bar
        self.root.attributes("-topmost", True)  # Always on top
        self.root.attributes("-alpha", self.config.get("opacity", 0.88))

        # Windows transparent color trick
        if sys.platform == "win32":
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        self.root.configure(bg=TRANSPARENT_KEY)

        # Position
        pos = self.config.get("position")
        if pos:
            self.root.geometry(f"+{pos['x']}+{pos['y']}")
        else:
            # Center on screen
            self.root.update_idletasks()
            w, h = 300, 60
            x = (self.root.winfo_screenwidth() - w) // 2
            y = (self.root.winfo_screenheight() - h) // 4
            self.root.geometry(f"{w}x{h}+{x}+{y}")

        # Bind window close → hide to tray
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)

        # Scroll wheel → adjust opacity
        self.root.bind("<MouseWheel>", self._on_scroll)

        # Right-click → context menu
        self._build_context_menu()
        self.root.bind("<Button-3>", self._on_right_click)

    def _build_labels(self):
        """Create floating text labels for each stock."""
        self.labels_frame = tk.Frame(self.root, bg=TRANSPARENT_KEY)
        self.labels_frame.pack(fill="both", expand=True, padx=4, pady=2)

        self.labels = {}
        stocks = self.config.get("stocks", [])
        for stock in stocks:
            code = stock["code"]
            lbl = tk.Label(
                self.labels_frame,
                text="加载中...",
                font=("Consolas", self.config.get("font_size", 11), "bold"),
                fg="#000000",
                bg=TRANSPARENT_KEY,
                anchor="w",
            )
            lbl.pack(anchor="w")
            # Bind drag to label too
            lbl.bind("<Button-1>", self._on_press)
            lbl.bind("<B1-Motion>", self._on_drag)
            lbl.bind("<Button-3>", self._on_right_click)
            self.labels[code] = lbl

    def _build_context_menu(self):
        """Build right-click context menu (custom popup, not tk.Menu)."""
        self._popup = None

    def _show_popup(self, x, y):
        """Show custom popup menu at (x, y)."""
        # Destroy previous popup if exists
        self._close_popup()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#2b2b2b")
        self._popup = popup

        def make_btn(text, cmd):
            btn = tk.Label(popup, text=text, font=("Segoe UI", 10),
                           fg="#ffffff", bg="#2b2b2b", anchor="w",
                           padx=16, pady=6, cursor="hand2")
            btn.pack(fill="x")
            btn.bind("<Enter>", lambda e: btn.configure(bg="#404040"))
            btn.bind("<Leave>", lambda e: btn.configure(bg="#2b2b2b"))
            def on_click(e=None):
                self._close_popup()
                self.root.after(30, cmd)
            btn.bind("<Button-1>", on_click)
            return btn

        make_btn("Show / Hide", self._toggle_visibility)
        make_btn("Settings...", self._open_settings)
        # Separator
        sep = tk.Frame(popup, bg="#555555", height=1)
        sep.pack(fill="x", padx=8, pady=2)
        make_btn("Quit", self._quit_app)

        # Position popup — flip if near screen edge
        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        # Flip horizontal if overflowing right edge
        if x + pw > sw:
            x = x - pw
        # Flip vertical if overflowing bottom edge
        if y + ph > sh:
            y = y - ph
        # Clamp to screen
        x = max(0, x)
        y = max(0, y)
        popup.geometry(f"+{x}+{y}")

        # Close on click outside — poll focus every 200ms
        def check_focus():
            if self._popup and self._popup.winfo_exists():
                try:
                    # If popup has no focused widget, user clicked elsewhere
                    focused = self._popup.focus_get()
                    if focused is None:
                        self._close_popup()
                        return
                except Exception:
                    self._close_popup()
                    return
                self._popup.after(200, check_focus)
        self._popup.after(200, check_focus)

    def _close_popup(self):
        """Dismiss the custom popup menu."""
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None

    def _build_tray(self):
        """Build system tray icon with pystray."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Create a simple tray icon
            def make_icon():
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                # Simple chart-like icon
                d.rectangle([8, 8, 56, 56], fill="#1a1a2e", outline="#e94560", width=2)
                d.line([16, 44, 28, 28, 40, 36, 52, 18], fill="#e94560", width=3)
                return img

            menu = pystray.Menu(
                pystray.MenuItem("Show / Hide", self._tray_toggle),
                pystray.MenuItem("Settings", self._tray_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._tray_quit),
            )

            self.tray_icon = pystray.Icon(
                APP_NAME, make_icon(), APP_NAME, menu
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

        except ImportError:
            print("[Warning] pystray not installed — system tray disabled", file=sys.stderr)
            print("  Install: pip install pystray Pillow", file=sys.stderr)
            self.tray_icon = None

    # ── Tray Callbacks (thread-safe) ───────────────────────────────

    def _tray_toggle(self, icon=None, item=None):
        self.root.after(0, self._toggle_visibility)

    def _tray_settings(self, icon=None, item=None):
        self.root.after(0, self._open_settings)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._quit_app)

    # ── Window Actions ─────────────────────────────────────────────

    def _toggle_visibility(self):
        if self.visible:
            self._hide_window()
        else:
            self._show_window()

    def _hide_window(self):
        self.root.withdraw()
        self.visible = False

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.visible = True

    def _quit_app(self):
        self.running = False
        # Save current position
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            self.config["position"] = {"x": x, "y": y}
            save_config(self.config)
        except Exception:
            pass
        # Stop tray
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    # ── Drag ───────────────────────────────────────────────────────

    def _on_press(self, e):
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    def _on_drag(self, e):
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self._drag_x = e.x_root
        self._drag_y = e.y_root

    # ── Scroll → Opacity ───────────────────────────────────────────

    def _on_scroll(self, e):
        delta = 0.05 if e.delta > 0 else -0.05
        opacity = max(0.2, min(1.0, self.config.get("opacity", 0.88) + delta))
        self.config["opacity"] = round(opacity, 2)
        self.root.attributes("-alpha", opacity)

    # ── Right-Click Menu ───────────────────────────────────────────

    def _on_right_click(self, e):
        self._show_popup(e.x_root, e.y_root)

    # ── Settings Dialog ────────────────────────────────────────────

    def _open_settings(self):
        """Open settings dialog to manage watchlist."""
        SettingsDialog(self.root, self.config, self._on_settings_saved)

    def _on_settings_saved(self, new_config):
        """Callback after settings are saved."""
        self.config = new_config
        save_config(new_config)
        # Rebuild labels
        for lbl in self.labels.values():
            lbl.destroy()
        self.labels.clear()
        self._build_labels()
        # Auto-resize window to fit new labels
        self.root.update_idletasks()
        self.root.geometry("")  # Let tkinter auto-calculate size
        # Force immediate refresh
        threading.Thread(target=self._fetch_once, daemon=True).start()

    # ── Data Fetch Loop ────────────────────────────────────────────

    def _fetch_loop(self):
        """Background thread: fetch data periodically."""
        while self.running:
            self._fetch_once()
            # Sleep in small chunks for responsive shutdown
            interval = self.config.get("refresh_interval", REFRESH_INTERVAL)
            for _ in range(interval * 2):
                if not self.running:
                    return
                time.sleep(0.5)

    def _fetch_once(self):
        """Fetch quotes and update UI."""
        codes = [s["code"] for s in self.config.get("stocks", [])]
        if not codes:
            return

        data = fetch_quotes(codes)
        if data and self.running:
            self.root.after(0, self._update_display, data)

    def _update_display(self, data: dict):
        """Update label text with latest data (must run on main thread)."""
        for stock in self.config.get("stocks", []):
            code = stock["code"]
            if code in data and code in self.labels:
                info = data[code]
                pct = info["change_pct"]
                text = format_pct(pct)
                # US stocks: append pre/post market data if enabled
                if self.config.get("show_extended_hours", True):
                    extra = info.get("extra", {})
                    if extra:
                        ah_pct = extra.get("ah_pct")
                        pm_pct = extra.get("pm_pct")
                        ah_time = extra.get("ah_time", "")
                        if pm_pct is not None:
                            text += f"  PM {format_pct(pm_pct)}"
                        elif ah_pct is not None:
                            label = "夜盘" if ah_time == "overnight" else "AH"
                            text += f"  {label} {format_pct(ah_pct)}"
                self.labels[code].config(text=text)

    # ── Run ────────────────────────────────────────────────────────

    def run(self):
        """Start the application."""
        self.root.mainloop()


# ── Settings Dialog ───────────────────────────────────────────────
class SettingsDialog:
    """Settings dialog for managing the watchlist."""

    def __init__(self, parent, config, on_save):
        self.config = config
        self.on_save = on_save
        self.result = None

        # Create dialog window
        self.win = tk.Toplevel(parent)
        self.win.title("Settings — Fish Monitor")
        self.win.geometry("420x620")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        # Make modal
        self.win.transient(parent)
        self.win.grab_set()

        self._build_ui()
        self.win.wait_window()

    def _build_ui(self):
        # ── Stock List ──
        list_frame = tk.LabelFrame(self.win, text="Watchlist", padx=8, pady=8)
        list_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        self.listbox = tk.Listbox(list_frame, height=8, font=("Consolas", 10))
        self.listbox.pack(fill="both", expand=True)

        for stock in self.config.get("stocks", []):
            self.listbox.insert(tk.END, f"{stock['code']}  —  {stock['name']}")

        # ── Add/Remove Buttons ──
        btn_frame = tk.Frame(list_frame)
        btn_frame.pack(fill="x", pady=(8, 0))

        tk.Button(btn_frame, text="Add Stock", command=self._custom_add).pack(side="left", padx=(0, 4))
        tk.Button(btn_frame, text="Remove", command=self._remove_stock).pack(side="left")

        # ── Refresh Interval ──
        interval_frame = tk.LabelFrame(self.win, text="Refresh", padx=8, pady=8)
        interval_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(interval_frame, text="Interval (seconds):").pack(side="left")
        self.interval_var = tk.StringVar(value=str(self.config.get("refresh_interval", 10)))
        tk.Entry(interval_frame, textvariable=self.interval_var, width=6).pack(side="left", padx=(8, 0))

        # ── Font Size ──
        font_frame = tk.LabelFrame(self.win, text="Display", padx=8, pady=8)
        font_frame.pack(fill="x", padx=10, pady=5)

        row1 = tk.Frame(font_frame)
        row1.pack(fill="x")
        tk.Label(row1, text="Font size:").pack(side="left")
        self.font_var = tk.StringVar(value=str(self.config.get("font_size", 11)))
        tk.Entry(row1, textvariable=self.font_var, width=6).pack(side="left", padx=(8, 0))

        # Extended hours toggle — its own row
        self.ext_hours_var = tk.BooleanVar(value=self.config.get("show_extended_hours", True))
        tk.Checkbutton(font_frame, text="Show overnight / pre / post market",
                       variable=self.ext_hours_var).pack(anchor="w", pady=(6, 0))

        # ── Longbridge API (optional, for real-time overnight) ──
        lp_frame = tk.LabelFrame(self.win, text="Longbridge API (optional, for US overnight)", padx=8, pady=8)
        lp_frame.pack(fill="x", padx=10, pady=5)

        lp_creds = self.config.get("longport", {})
        lp_status = "Connected" if lp_creds.get("app_key") else "Not configured"

        tk.Label(lp_frame, text=f"Status: {lp_status}", fg="green" if "Connected" in lp_status else "gray").pack(anchor="w")

        key_frame = tk.Frame(lp_frame)
        key_frame.pack(fill="x", pady=(4, 0))
        tk.Label(key_frame, text="App Key:").pack(side="left")
        self.lp_key_var = tk.StringVar(value=lp_creds.get("app_key", ""))
        tk.Entry(key_frame, textvariable=self.lp_key_var, width=30, show="*").pack(side="left", padx=(4, 0), fill="x", expand=True)

        secret_frame = tk.Frame(lp_frame)
        secret_frame.pack(fill="x", pady=(2, 0))
        tk.Label(secret_frame, text="Secret:").pack(side="left")
        self.lp_secret_var = tk.StringVar(value=lp_creds.get("app_secret", ""))
        tk.Entry(secret_frame, textvariable=self.lp_secret_var, width=30, show="*").pack(side="left", padx=(4, 0), fill="x", expand=True)

        token_frame = tk.Frame(lp_frame)
        token_frame.pack(fill="x", pady=(2, 0))
        tk.Label(token_frame, text="Token:").pack(side="left")
        self.lp_token_var = tk.StringVar(value=lp_creds.get("access_token", ""))
        tk.Entry(token_frame, textvariable=self.lp_token_var, width=30, show="*").pack(side="left", padx=(4, 0), fill="x", expand=True)

        # ── Save / Cancel ──
        bottom_frame = tk.Frame(self.win)
        bottom_frame.pack(fill="x", padx=10, pady=(5, 10))

        tk.Button(bottom_frame, text="Save", command=self._save, width=10).pack(side="right", padx=(4, 0))
        tk.Button(bottom_frame, text="Cancel", command=self.win.destroy, width=10).pack(side="right")

    def _custom_add(self):
        """Add a custom stock code with auto-fetched name."""
        code = simpledialog.askstring("Add Stock", "Enter stock code:\n\n"
                                       "A-share: s_sh000001 (Shanghai) / s_sz399001 (Shenzhen)\n"
                                       "HK: hk01810 (Xiaomi) / hkHSI (Hang Seng)\n"
                                       "US: gb_skdd / gb_aapl / gb_$dji",
                                       parent=self.win)
        if code:
            code = code.strip().lower()
            if code:
                # Auto-fetch stock name, reject if no data
                try:
                    data = fetch_quotes([code])
                    if code in data:
                        name = data[code]["name"]
                        self._add_stock(code, name)
                    else:
                        messagebox.showerror("Error", f"Cannot find: {code}\n\n"
                                             "Check the code format:\n"
                                             "  A-share: s_sh000001\n"
                                             "  HK: hk01810\n"
                                             "  US: gb_skdd", parent=self.win)
                except Exception as e:
                    messagebox.showerror("Error", f"Fetch failed: {e}", parent=self.win)

    def _add_stock(self, code, name):
        if "stocks" not in self.config:
            self.config["stocks"] = []
        existing = [s["code"] for s in self.config["stocks"]]
        if code in existing:
            return
        self.config["stocks"].append({"code": code, "name": name})
        self.listbox.insert(tk.END, f"{code}  —  {name}")

    def _remove_stock(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.listbox.delete(idx)
        self.config["stocks"].pop(idx)

    def _save(self):
        try:
            interval = int(self.interval_var.get())
            if interval < 3:
                interval = 3
            self.config["refresh_interval"] = interval
        except ValueError:
            pass

        try:
            font_size = int(self.font_var.get())
            if font_size < 8:
                font_size = 8
            if font_size > 24:
                font_size = 24
            self.config["font_size"] = font_size
        except ValueError:
            pass

        # Save extended hours toggle
        self.config["show_extended_hours"] = self.ext_hours_var.get()

        # Save Longbridge API credentials
        lp_key = self.lp_key_var.get().strip()
        lp_secret = self.lp_secret_var.get().strip()
        lp_token = self.lp_token_var.get().strip()
        if lp_key and lp_secret and lp_token:
            self.config["longport"] = {
                "app_key": lp_key,
                "app_secret": lp_secret,
                "access_token": lp_token,
            }
        elif "longport" in self.config:
            # Clear if fields are empty
            del self.config["longport"]

        # Reset longport context on save
        import fish_monitor as _fm
        _fm._longport_ctx = None

        self.on_save(self.config)
        self.win.destroy()


# ── Entry Point ───────────────────────────────────────────────────
if __name__ == "__main__":
    app = FishMonitor()
    app.run()
