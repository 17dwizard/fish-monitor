# Fish Monitor - 摸鱼看盘小工具

透明背景 + 纯黑文字 + 只显示百分比 + 系统托盘

## Quick Start

1. Install Python 3.8+ on Windows
2. Install dependencies:
   ```
   pip install pystray Pillow longport
   ```
3. Double-click `run.bat`

## Features

- **透明浮动窗口** — 桌面上只看到涨跌百分比数字
- **纯黑文字** — 涨跌都用黑色，不红不绿，低调摸鱼
- **无标题栏** — 不占任务栏，pythonw.exe静默启动
- **系统托盘** — 右下角小图标，Show/Hide/Settings/Quit
- **可拖拽** — 鼠标拖动窗口位置
- **滚轮调透明度** — 鼠标滚轮上下调整
- **右键菜单** — Show/Hide / Settings / Quit
- **自动保存** — 窗口位置、配置自动保存
- **实时夜盘** — 配置长桥API后，美股显示实时夜盘/盘前/盘后涨跌

## Data Sources

| Market | Source | Auth Required |
|--------|--------|---------------|
| A-share | Sina Finance | No |
| Hong Kong | Tencent Finance | No |
| US (basic) | Sina Finance | No |
| US (overnight) | Longbridge SDK | Yes |

## Longbridge API Setup (optional, for US overnight)

1. Get API credentials from https://open.longportapp.com/
2. Open Settings → Longbridge API section
3. Fill in App Key, Secret, and Access Token
4. Save → US stocks will now show real-time overnight/pre/post market data

## Stock Codes (Sina Finance)

| Market | Format | Example |
|--------|--------|---------|
| A-share Shanghai | s_sh + code | s_sh000001 (上证指数) |
| A-share Shenzhen | s_sz + code | s_sz399001 (深证成指) |
| Hong Kong | hk + code | hk01810 (小米) |
| US | gb_ + code | gb_aapl (Apple) |

## Default

预设只有上证指数，其余通过 Settings → Quick Add 添加。

## Files

- `fish_monitor.py` — Main program
- `config.json` — Auto-generated config file
- `run.bat` — Launcher (uses pythonw.exe)
