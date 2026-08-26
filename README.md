# 🐟 Fish Monitor

摸鱼看盘小工具 — 透明浮动窗口，桌面上只显示涨跌百分比。

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Features

- **透明背景** — 纯黑文字浮动在桌面上，低调摸鱼
- **只显示百分比** — 不显示股票名称，涨跌一目了然
- **无标题栏** — 不占任务栏，pythonw.exe 静默启动
- **系统托盘** — 右下角小图标，Show/Hide/Settings/Quit
- **可拖拽** — 鼠标拖动窗口位置
- **滚轮调透明度** — 鼠标滚轮上下调整
- **多市场支持** — A股、港股、美股
- **实时夜盘** — 配置 Longbridge API 后显示美股夜盘/盘前/盘后涨跌

## 📦 Data Sources

| Market | Source | Auth |
|--------|--------|------|
| A-share | Sina Finance | ❌ |
| Hong Kong | Tencent Finance | ❌ |
| US (basic) | Sina Finance | ❌ |
| US (overnight) | Longbridge SDK | ✅ |

## 🚀 Quick Start

### 1. Install Python 3.8+

Download from [python.org](https://www.python.org/downloads/)

### 2. Install dependencies

```bash
pip install pystray Pillow
```

For real-time US overnight data (optional):
```bash
pip install longbridge
```

### 3. Run

Double-click `run.bat` or:
```bash
python fish_monitor.py
```

## ⚙️ Configuration

Right-click the floating widget → **Settings**:

- **Add Stock** — Enter stock code (auto-fetches name)
- **Remove** — Remove selected stock
- **Font size** — Adjust text size
- **Show overnight/pre/post market** — Toggle extended hours display
- **Longbridge API** — Enter credentials for real-time US overnight data

### Stock Code Format

| Market | Format | Example |
|--------|--------|---------|
| A-share Shanghai | `s_sh` + code | `s_sh000001` (上证指数) |
| A-share Shenzhen | `s_sz` + code | `s_sz399001` (深证成指) |
| Hong Kong | `hk` + code | `hk01810` (小米) |
| US | `gb_` + code | `gb_skdd` / `gb_aapl` |

## 📁 Files

```
fish_monitor.py    — Main application
run.bat            — Windows launcher (pythonw.exe)
config.json        — Auto-generated config
README.md          — This file
```

## 🔧 Longbridge API Setup (optional)

For real-time US overnight/pre/post market data:

1. Get API credentials from [open.longbridge.com](https://open.longbridge.com/)
2. Right-click → Settings → Longbridge API
3. Fill in App Key, Secret, and Access Token

## 📝 License

MIT
