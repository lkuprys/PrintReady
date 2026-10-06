# PrintReady PRO

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![UI](https://img.shields.io/badge/UI-PySide6-171717.svg)](https://doc.qt.io/qtforpython-6/)
[![Color Profile](https://img.shields.io/badge/ICC-U.S.%20Web%20Coated%20SWOP%20v2-green.svg)](https://www.color.org)
[![Output Format](https://img.shields.io/badge/Output-CMYK%20%2B%20Spot%20White%20TIFF-orange.svg)](https://www.adobe.com/products/photoshop.html)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

**PrintReady PRO** is a professional, high-throughput automated UV print file preparation and crop engine designed for on-demand manufacturing pipelines. It automates product mask cropping, RGB-to-CMYK color conversion with embedded ICC profiles, and exact Spot White (`W`) underbase channel generation for RIP software such as **ColorGATE** and **Adobe Photoshop**.

---

## 🌟 Key Features

- **Calm, modern UI** (Podbase WORK style): light theme by default (dark optional), top bar with underline tabs, Inter font. All colors and components live in `ui_theme.py`.
- **Accurate CMYK Color Conversion**:
  - Embedded **U.S. Web Coated (SWOP) v2** ICC profile (`Tag 34675`).
  - Perceptual rendering intent preventing color clipping and muddiness.
- **RIP-Ready Spot White (`W`) Underbase Channel**:
  - Produces a 6-channel TIFF (Cyan, Magenta, Yellow, Black, Transparency, Spot White).
  - Configurable white ink choke reduction (default: `1 px`) to avoid white bleed on edges.
  - Configurable spot channel solidity (default: `5%`) with Photoshop 8BIM metadata (`Tag 34377`), InkSet (`Tag 332`), and InkNames (`Tag 333`).
- **Dual Input Folders** (assignable in Settings or directly on the Orders page):
  - **Generacijos**: new client orders, ready print files saved to `READY/`.
  - **Rejected**: reprints/rejects, automatically routed into `READY/BROKAI/`.
- **Background Auto-Production**:
  - By default only **today's** orders are produced automatically (switch in Settings); older orders within the age limit are listed and can be produced manually.
  - Files are produced only after they have finished copying; failed files are retried with back-off instead of every cycle.
  - Output TIFFs are written to a temporary file and renamed when complete, so a half-written file is never visible.
- **Per-template rules** (Šablonai tab, stored in `Sablonai/sablonu_nustatymai.json`): rotate the whole print file (0/90/180/270°), rotate only the customer image inside the contour, or mirror – with a live preview.
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
PrintReady/
├── assets/                       # Brand logos, application icons, and splash screen
├── Sablonai/                     # Product contour PNG templates directory
├── crop_engine.py                # CMYK conversion, ICC embedding & Spot W TIFF writer
├── order_watcher.py              # Folder scanning, grouping & background auto-production
├── template_manager.py           # Template discovery and regex path matching
├── template_options.py           # Per-template rules (rotation, mirror)
├── updater.py                    # GitHub Releases auto-updater (APP_VERSION)
├── main.py                       # Main application entry point & UI
├── ui_theme.py                   # Colors, fonts, stylesheet and shared widgets
├── fluent_gui.py                 # Compatibility launcher (imports main.py)
├── us_web_coated_swop_v2.icc     # Official CMYK color profile
├── build.py / build_exe.bat      # PyInstaller build (folder build)
├── package_release.py            # Release ZIP packaging and verification
├── Idiegti.bat                   # Installer to C:\Podbase\PrintReady
├── tests/                        # Unit tests (run without Windows / Qt)
├── requirements.txt              # Python package dependencies
└── LICENSE                       # MIT License
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Operating System**: Windows 10 / 11 (64-bit)
- **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14 (64-bit)

### 2. Installation
Clone the repository and install the dependencies:

```bash
git clone https://github.com/lkuprys/PrintReady.git
cd PrintReady
pip install -r requirements.txt
```

### 3. Running the Application
Launch the application from the terminal:

```bash
python main.py
```

---

## 🔨 Compiling Standalone Executable (.EXE)

To build `dist/PrintReady/PrintReady.exe` (with `_internal/`) and the release ZIP locally:

```cmd
build_exe.bat
```

Official releases are built on GitHub: **Actions → „Išleisti naują versiją“ → Run workflow**.

### Tests

```bash
python -m unittest discover -s tests -v
```

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

0. **Input folders**: Assign the **Generacijos** and **Rejected** folders on the Orders page (or in Settings).
1. **Scan Orders**: Click **🔍 SKENUOTI UŽSAKYMUS** in the Orders tab. The system scans both Generacijos and Rejected.
2. **Review Groups**: Orders are displayed in clear high-contrast cards (Standard = Green, Rejects = Red, Missing Template = Amber).
3. **Produce**: Select items and click **🚀 GAMINTI PAŽYMĖTUS**. Ready TIFF files are saved to `READY/` and `READY/BROKAI/`.
4. **Automated Background Watching**: Toggle **Automatinis Fono Stebėjimas** in Settings to automatically process new incoming files in real-time. With **Tik šiandien** enabled (default) only today's orders are produced automatically.

---

## 📄 License

This project is open-source software licensed under the [MIT License](LICENSE).
