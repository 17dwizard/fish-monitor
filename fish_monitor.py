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
TRANSPARENT_KEY = "#101010"  # Windows transparent color key — kept CLOSE to the text
# color on purpose: the color-key technique leaves anti-aliasing transition
# pixels (blend of text color and key color) visible, so a light key produces
# a bright halo around glyphs. With a near-black key those transition pixels
# blend toward the glyph itself and the halo disappears.
REFRESH_INTERVAL = 10  # seconds

# ── Default Config ─────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "stocks": [
        {"code": "s_sh000001", "name": "上证指数"}
    ],
    "opacity": 0.88,
    "position": None,  # {x, y} or None = center
    "font_size": 11,
    "text_color": "#000000",  # use "#ffffff" on dark desktops
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


# ── Anti-aliased Text Rendering (Pillow) ───────────────────────────
# GDI text via tk.Label has no anti-aliasing and looks flat. Instead we
# render text with Pillow (2x supersampled, LANCZOS downscale) onto the
# transparent-key color and display it as a label image.
FONTS_DIR = Path(__file__).parent / "fonts"

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = ImageDraw = ImageFont = ImageTk = None

_KEY_RGB = (16, 16, 16)  # must match TRANSPARENT_KEY (#101010)


def _parse_color(value, fallback=(0, 0, 0)):
    """Parse '#rrggbb' into an RGB tuple."""
    try:
        s = value.lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def _dpi_scale_of(widget) -> float:
    """DPI scale factor of the widget's display (1.0 = 96 DPI)."""
    try:
        return float(widget.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0


def _split_runs(text: str):
    """Split text into ('num', seg) / ('cjk', seg) runs for per-script fonts."""
    runs = []
    for ch in text:
        kind = "cjk" if ord(ch) > 0x2E80 else "num"
        if runs and runs[-1][0] == kind:
            runs[-1][1] += ch
        else:
            runs.append([kind, ch])
    return runs


class TextRenderer:
    """Render label text with Pillow onto the transparent-key background."""

    _S = 2  # supersample factor

    def __init__(self, config, dpi_scale: float = 1.0):
        pt = config.get("font_size", 11)
        self.px = max(8, int(round(pt * 4 / 3 * dpi_scale)))
        text_rgb = _parse_color(config.get("text_color", "#000000"))
        # text color must differ from the transparent key, or glyphs go invisible
        self._text_rgb = (0, 0, 0) if text_rgb == _KEY_RGB else text_rgb
        size = self.px * self._S
        self._f_num = self._load_font(
            config.get("number_font", "ChakraPetch-Bold.ttf"),
            ["C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/arialbd.ttf"],
            size,
        )
        self._f_cjk = self._load_font(
            config.get("cjk_font", "NotoSansSC.ttf"),
            ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"],
            size,
            variation="Medium",
        )
        if self._f_num is None or self._f_cjk is None:
            raise RuntimeError("no usable fonts found")
        # Shared baseline so mixed CJK/Latin runs sit level
        asc_n, desc_n = self._f_num.getmetrics()
        asc_c, desc_c = self._f_cjk.getmetrics()
        self._baseline = max(asc_n, asc_c) + self._S * 2
        self._height = self._baseline + max(desc_n, desc_c) + self._S * 2

    @staticmethod
    def _load_font(filename, fallbacks, size, variation=None):
        candidates = [FONTS_DIR / filename] + [Path(p) for p in fallbacks]
        for path in candidates:
            try:
                font = ImageFont.truetype(str(path), size)
                if variation:
                    try:
                        font.set_variation_by_name(variation)
                    except Exception:
                        pass
                return font
            except Exception:
                continue
        return None

    def render(self, text: str):
        """Return an ImageTk.PhotoImage of anti-aliased text (caller keeps ref)."""
        S = self._S
        tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        runs = _split_runs(text)
        width = sum(tmp.textlength(seg, font=(self._f_cjk if k == "cjk" else self._f_num))
                    for k, seg in runs)
        w = int(width) + 2 * S
        layer = Image.new("RGBA", (w, self._height), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        x = float(S)
        for kind, seg in runs:
            f = self._f_cjk if kind == "cjk" else self._f_num
            d.text((x, self._baseline), seg, font=f, fill=self._text_rgb + (255,), anchor="ls")
            x += d.textlength(seg, font=f)
        base = Image.new("RGBA", layer.size, _KEY_RGB + (255,))
        out = Image.alpha_composite(base, layer)
        out = out.resize((max(1, w // S), max(1, self._height // S)), Image.LANCZOS)
        return ImageTk.PhotoImage(out)


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


def _detect_proxy():
    """Detect local proxy (Clash, V2Ray, etc.) and set env vars."""
    import socket
    common_proxies = [
        ("http://127.0.0.1:7890", 7890),   # Clash default
        ("http://127.0.0.1:7897", 7897),   # Clash alternative
        ("http://127.0.0.1:1080", 1080),   # V2Ray/SS common
        ("http://127.0.0.1:10809", 10809), # V2Ray default
        ("socks5://127.0.0.1:7891", 7891), # Clash SOCKS5
        ("socks5://127.0.0.1:1080", 1080), # SS SOCKS5
    ]
    for proxy_url, port in common_proxies:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["ALL_PROXY"] = proxy_url
            os.environ["WS_PROXY"] = proxy_url
            os.environ["WSS_PROXY"] = proxy_url
            print(f"[Proxy] Detected local proxy: {proxy_url}", flush=True)
            return proxy_url
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"[Proxy] Port {port}: {e}", flush=True)
            continue
    print("[Proxy] No local proxy detected", flush=True)
    return None


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
        print("[Longport] No credentials configured", file=sys.stderr)
        return None

    # Auto-detect proxy for Clash/V2Ray users
    _detect_proxy()

    # Set region for China mainland (SDK auto-selects .cn endpoints)
    os.environ.setdefault("LONGBRIDGE_REGION", "cn")

    try:
        from longbridge.openapi import Config, QuoteContext
        config = Config.from_apikey(
            app_key,
            app_secret,
            access_token,
            enable_overnight=True,
        )
        _longport_ctx = QuoteContext(config)
        print("[Longport] Connected successfully", flush=True)
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

            # Overnight / pre / post market (SDK fields: overnight_quote, pre_market_quote, post_market_quote)
            extra = {}
            if hasattr(q, "overnight_quote") and q.overnight_quote:
                oh = q.overnight_quote
                oh_price = float(oh.last_done)
                oh_prev = float(oh.prev_close)
                extra["on_price"] = oh_price
                extra["on_pct"] = (oh_price - oh_prev) / oh_prev * 100 if oh_prev else 0
            if hasattr(q, "pre_market_quote") and q.pre_market_quote:
                pm = q.pre_market_quote
                pm_price = float(pm.last_done)
                pm_prev = float(pm.prev_close)
                extra["pm_price"] = pm_price
                extra["pm_pct"] = (pm_price - pm_prev) / pm_prev * 100 if pm_prev else 0
            if hasattr(q, "post_market_quote") and q.post_market_quote:
                pt = q.post_market_quote
                pt_price = float(pt.last_done)
                pt_prev = float(pt.prev_close)
                extra["ah_price"] = pt_price
                extra["ah_pct"] = (pt_price - pt_prev) / pt_prev * 100 if pt_prev else 0

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


def _nth_weekday(year: int, month: int, weekday: int, n: int):
    """Return the n-th given weekday (Mon=0..Sun=6) of a month as a datetime."""
    from datetime import datetime, timedelta
    d = datetime(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _us_is_edt(now_utc=None) -> bool:
    """Is US Eastern currently in daylight saving time (EDT)?

    US DST rules (since 2007): 2nd Sunday of March 02:00 EST -> 1st Sunday of
    November 02:00 EDT. Compared in UTC (07:00 / 06:00) to stay unambiguous.
    """
    from datetime import datetime, timezone
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    dst_start = _nth_weekday(year, 3, 6, 2).replace(hour=7, tzinfo=timezone.utc)   # 2nd Sun Mar 07:00 UTC
    dst_end = _nth_weekday(year, 11, 6, 1).replace(hour=6, tzinfo=timezone.utc)     # 1st Sun Nov 06:00 UTC
    return dst_start <= now_utc < dst_end


def _active_us_session() -> str:
    """Determine which US stock session is currently active (Shanghai time UTC+8).

    US sessions in Beijing time (EDT, ~Mar-Nov):
        Overnight:  08:00 - 16:00
        Pre-market: 16:00 - 21:30
        Regular:    21:30 - 04:00 (next day)
        Post-market: 04:00 - 08:00

    US sessions in Beijing time (EST, ~Nov-Mar):
        Overnight:  09:00 - 17:00
        Pre-market: 17:00 - 22:30
        Regular:    22:30 - 05:00 (next day)
        Post-market: 05:00 - 09:00

    DST is auto-detected via _us_is_edt(), so the schedule flips correctly
    on transition days instead of being hardcoded to one season.
    """
    from datetime import datetime
    now = datetime.now()
    t = now.hour * 60 + now.minute

    if _us_is_edt():
        # EDT schedule
        if 480 <= t < 960:    return "overnight"   # 08:00–16:00
        if 960 <= t < 1290:   return "pre_market"  # 16:00–21:30
        if t >= 1290 or t < 240: return "regular"  # 21:30–04:00
        return "post_market"                       # 04:00–08:00
    # EST schedule
    if 540 <= t < 1020:   return "overnight"       # 09:00–17:00
    if 1020 <= t < 1350:  return "pre_market"      # 17:00–22:30
    if t >= 1350 or t < 300: return "regular"      # 22:30–05:00
    return "post_market"                           # 05:00–09:00


# ── Main Application ──────────────────────────────────────────────
class FishMonitor:
    def __init__(self):
        self.config = load_config()
        self.running = True
        self.visible = True

        # Text rendering state (Pillow-based, see TextRenderer)
        self._renderer = None
        self._renderer_key = None
        self._photos = {}  # keep PhotoImage refs alive (GC guard)

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
        # DPI awareness must be set before the Tk window exists, otherwise
        # Windows bitmap-stretches everything and text renders blurry.
        if sys.platform == "win32":
            try:
                import ctypes
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
                except Exception:
                    ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)  # No title bar
        self.root.attributes("-topmost", True)  # Always on top
        self.root.attributes("-alpha", self.config.get("opacity", 0.88))

        # DPI scale for crisp Pillow text rendering (physical px per logical px)
        try:
            self._dpi_scale = float(self.root.winfo_fpixels("1i")) / 96.0
        except Exception:
            self._dpi_scale = 1.0

        # Windows transparent color trick
        if sys.platform == "win32":
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        self.root.configure(bg=TRANSPARENT_KEY)

        # Position
        pos = self.config.get("position")
        if pos:
            self.root.geometry(f"+{pos['x']}+{pos['y']}")
        else:
            # Center on screen (scale with DPI — geometry is physical pixels)
            self.root.update_idletasks()
            w, h = int(300 * self._dpi_scale), int(60 * self._dpi_scale)
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
                fg=self.config.get("text_color", "#000000"),
                bg=TRANSPARENT_KEY,
                anchor="w",
            )
            lbl.pack(anchor="w")
            # Bind drag to label too
            lbl.bind("<Button-1>", self._on_press)
            lbl.bind("<B1-Motion>", self._on_drag)
            lbl.bind("<Button-3>", self._on_right_click)
            self.labels[code] = lbl
            self._set_label(code, "加载中...")

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
                # US stocks only: show whichever session is currently active
                if self.config.get("show_extended_hours", True) and code.startswith("gb_"):
                    extra = info.get("extra", {})
                    session = _active_us_session()
                    on_pct = extra.get("on_pct") if extra else None
                    pm_pct = extra.get("pm_pct") if extra else None
                    ah_pct = extra.get("ah_pct") if extra else None
                    if session == "overnight":
                        # 夜盘优先用夜盘数据，没有就用盘后数据兜底
                        pct_val = on_pct if on_pct is not None else ah_pct
                        label = "夜盘"
                    elif session == "pre_market":
                        pct_val = pm_pct
                        label = "盘前"
                    elif session == "post_market":
                        pct_val = ah_pct
                        label = "盘后"
                    else:
                        pct_val = None
                        label = ""
                    if label:
                        text = f"{label} {format_pct(pct_val)}" if pct_val is not None else f"{label} --%"
                self._set_label(code, text)

    def _get_renderer(self):
        """Get (or rebuild) the Pillow text renderer; None falls back to GDI text."""
        if not HAS_PIL:
            return None
        want = (self.config.get("font_size", 11), round(getattr(self, "_dpi_scale", 1.0), 2))
        if self._renderer is None or self._renderer_key != want:
            try:
                self._renderer = TextRenderer(self.config, getattr(self, "_dpi_scale", 1.0))
            except Exception as e:
                print(f"[Font] renderer unavailable, falling back to GDI text: {e}", file=sys.stderr)
                self._renderer = None
            self._renderer_key = want
        return self._renderer

    def _set_label(self, code: str, text: str):
        """Update a label with Pillow-rendered text (or plain GDI text fallback)."""
        lbl = self.labels.get(code)
        if lbl is None:
            return
        renderer = self._get_renderer()
        if renderer is not None:
            photo = renderer.render(text)
            self._photos[code] = photo  # prevent GC of the PhotoImage
            lbl.config(image=photo, text="")
        else:
            lbl.config(text=text)

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
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        # Make modal
        self.win.transient(parent)
        self.win.grab_set()

        self._build_ui()

        # Size to content. Fixed pixel geometry clips on high-DPI displays:
        # tk fonts scale with the real DPI (process is DPI-aware) but a
        # geometry string like "420x620" is physical pixels. Scale the design
        # size and never go below what the content actually requests.
        self.win.update_idletasks()
        s = _dpi_scale_of(self.win)
        w = max(int(420 * s), self.win.winfo_reqwidth())
        h = max(int(620 * s), self.win.winfo_reqheight())
        self.win.geometry(f"{w}x{h}")

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
