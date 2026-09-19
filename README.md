# PrintReady PRO

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![UI](https://img.shields.io/badge/UI-PySide6%20%7C%20Fluent%20Design-0078D4.svg)](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
[![Color Profile](https://img.shields.io/badge/ICC-U.S.%20Web%20Coated%20SWOP%20v2-green.svg)](https://www.color.org)
[![Output Format](https://img.shields.io/badge/Output-CMYK%20%2B%20Spot%20White%20TIFF-orange.svg)](https://www.adobe.com/products/photoshop.html)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

**PrintReady PRO** is a professional, high-throughput automated UV print file preparation and crop engine designed for on-demand manufacturing pipelines. It automates product mask cropping, RGB-to-CMYK color conversion with embedded ICC profiles, and exact Spot White (`W`) underbase channel generation for RIP software such as **ColorGATE** and **Adobe Photoshop**.

---

## 🌟 Key Features

- **Windows 11 Fluent Design GUI**: High-contrast, dark-mode user interface built with `PySide6` and `PyQt-Fluent-Widgets`.
- **Accurate CMYK Color Conversion**:
  - Embedded **U.S. Web Coated (SWOP) v2** ICC profile (`Tag 34675`).
  - Perceptual rendering intent preventing color clipping and muddiness.
- **RIP-Ready Spot White (`W`) Underbase Channel**:
  - Produces a 6-channel TIFF (Cyan, Magenta, Yellow, Black, Transparency, Spot White).
  - Configurable white ink choke reduction (default: `1 px`) to avoid white bleed on edges.
  - Configurable spot channel solidity (default: `5%`) with Photoshop 8BIM metadata (`Tag 34377`), InkSet (`Tag 332`), and InkNames (`Tag 333`).
- **Dual Hotfolder Automation**:
  - **Standard Hotfolder**: Scans new client orders and saves ready print files directly to `READY/`.
  - **Rejects / Brokai Hotfolder**: Dedicated monitor for reprints/rejects, automatically routing output into `READY/BROKAI/`.
- **Intelligent Template Matching**:
  - Greedy regex pattern matching with alphanumeric and word boundary checks.
  - Automatically matches models (e.g., `A2681`, `1932`, `NEO`, `A2442`) from deep or flat directory hierarchies.
- **Production Center & Instant Search**:
  - Batch produce all or selected model groups with multi-threaded progress tracking.
  - Instant live search by date, generation, model name, file number, or reject tag.
  - Dedicated 1-file manual crop tool for custom artwork and individual reprints.
- **Persistent Configuration (`config.json`)**:
  - Automatic persistence of network paths, print parameters, and custom template directories.

---

## 📁 Repository Structure

```
PrintReady_GitHub_Repo/
├── assets/                       # Brand logos, application icons, and splash screen
│   ├── app_icon.ico
│   ├── app_icon.png
│   ├── printready_icon.png
│   ├── splash_bg.png
│   ├── podbase_logo_header.png
│   └── podbase_logo_darkmode.png
├── Sablonai/                     # Product contour PNG templates directory
│   ├── README.md                 # Template guidelines
│   └── .gitkeep
├── crop_engine.py                # CMYK conversion, ICC embedding & Spot W TIFF writer
├── order_watcher.py              # Hotfolder scanning, grouping & auto-watch daemon
├── template_manager.py           # Template discovery and regex path matching
├── main.py                       # Main application entry point & Fluent UI
├── fluent_gui.py                 # UI implementation module
├── us_web_coated_swop_v2.icc     # Official CMYK color profile
├── build_exe.bat                 # 1-click standalone PyInstaller compiler script
├── requirements.txt              # Python package dependencies
├── LICENSE                       # MIT License
└── README.md                     # Documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Operating System**: Windows 10 / 11 (64-bit)
- **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14 (64-bit)

### 2. Installation
Clone the repository and install the dependencies:

```bash
git clone https://github.com/lkuprys/PrintReady-PRO.git
cd PrintReady-PRO
pip install -r requirements.txt
```

### 3. Running the Application
Launch the application from the terminal:

```bash
python main.py
```

---

## 🔨 Compiling Standalone Executable (.EXE)

To compile a standalone `PrintReady.exe` with the native bootloader splash screen and bundled ICC profile:

```cmd
build_exe.bat
```

Or run PyInstaller manually:
```bash
pyinstaller --noconfirm --onefile --windowed --name "PrintReady" ^
  --icon "app_icon.ico" --splash "splash_bg.png" ^
  --add-data "app_icon.ico;." --add-data "app_icon.png;." ^
  --add-data "printready_icon.png;." --add-data "splash_bg.png;." ^
  --add-data "podbase_logo_header.png;." --add-data "podbase_logo_darkmode.png;." ^
  --add-data "podbase_logo_transparent.png;." --add-data "us_web_coated_swop_v2.icc;." ^
  --collect-all qfluentwidgets --collect-all PySide6 --collect-all PIL ^
  --collect-all tifffile --collect-all imagecodecs main.py
```
The resulting executable will be created in `dist/PrintReady.exe`.

---

## ⚙️ Technical Print Specification

| Parameter | Specification | Details |
| :--- | :--- | :--- |
| **Output Format** | TIFF (`.tif`) | 6-Channel Separated Raster |
| **Color Space** | CMYK (4 Channels) | `U.S. Web Coated (SWOP) v2` embedded (`Tag 34675`) |
| **Alpha / Mask** | 5th Channel | Transparency mask (Associated Alpha) |
| **Spot Channel** | 6th Channel (`W`) | Spot White underbase with 5% solidity |
| **Metadata Tags** | TIFF Tags 332, 333, 34377 | Full Adobe Photoshop & ColorGATE RIP compatibility |
| **Resolution** | 300 DPI | Configurable in Settings |
| **Choke** | 1 px (Default) | Inward choke prevents white outline on outer edges |

---

## 📖 Usage Guide

1. **Scan Orders**: Click **🔍 SKENUOTI UŽSAKYMUS** in the Orders tab. The system scans both standard orders and rejects.
2. **Review Groups**: Orders are displayed in clear high-contrast cards (Standard = Green, Rejects = Red, Missing Template = Amber).
3. **Produce**: Select items and click **🚀 GAMINTI PAŽYMĖTUS**. Ready TIFF files are saved to `READY/` and `READY/BROKAI/`.
4. **Automated Background Watching**: Toggle **Automatinis Fono Stebėjimas** in Settings to automatically process new incoming files in real-time.

---

## 📄 License

This project is open-source software licensed under the [MIT License](LICENSE).
