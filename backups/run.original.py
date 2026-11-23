"""
╔══════════════════════════════════════════════════════════════╗
║   AQACT — Air Quality Analysis & Compliance Assessment Tool  ║
║   Just run:  python run.py                                   ║
║   Then open: http://localhost:5000                           ║
╚══════════════════════════════════════════════════════════════╝

This script:
  1. Checks your Python version (3.8+)
  2. Auto-installs all required packages
  3. Downloads static assets (Plotly, Leaflet) once to /static/
  4. Starts the Flask web server
  5. Opens your browser automatically
"""

import sys
import os
import subprocess
import time

# ── 1. Python version check ──────────────────────────────────────────────────
if sys.version_info < (3, 8):
    print("❌  Python 3.8 or newer is required.")
    print(f"   You have Python {sys.version}")
    print("   Download: https://python.org/downloads")
    input("\nPress Enter to exit...")
    sys.exit(1)

print("=" * 60)
print("  AQACT — Air Quality Analysis & Compliance Assessment Tool")
print("=" * 60)
print(f"\n✅  Python {sys.version.split()[0]} detected")

# ── 2. Auto-install packages ─────────────────────────────────────────────────
PACKAGES = [
    "flask",
    "flask-cors",
    "pandas",
    "numpy",
    "scipy",
    "scikit-learn",
    "xgboost",
    "plotly",
    "openpyxl",
    "xlrd",
    "werkzeug",
    "statsmodels",   # for OLS trendlines in plotly express
]

print("\n📦  Checking / installing required packages...")
print("    (only takes a while the very first time)\n")

for pkg in PACKAGES:
    import_name = pkg.replace("-", "_").split(">=")[0]
    if pkg == "scikit-learn":
        import_name = "sklearn"
    try:
        __import__(import_name)
        print(f"   ✓  {pkg} already installed")
    except ImportError:
        print(f"   ↓  Installing {pkg}...", end="", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(" done")
        else:
            print(f" FAILED\n{result.stderr}")
            input("\nPress Enter to exit...")
            sys.exit(1)

# ── 3. Download static assets (once) ────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

ASSETS = [
    ("plotly-2.35.2.min.js",
     "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"),
    ("leaflet-1.9.4.js",
     "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"),
    ("leaflet-1.9.4.css",
     "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"),
    ("leaflet-heat.js",
     "https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js"),
]

try:
    import urllib.request
    for fname, url in ASSETS:
        fpath = os.path.join(STATIC_DIR, fname)
        if not os.path.exists(fpath):
            print(f"   ↓  Downloading {fname}...", end="", flush=True)
            try:
                urllib.request.urlretrieve(url, fpath)
                print(" done")
            except Exception as e:
                print(f" skipped ({e}) — CDN fallback will be used")
        else:
            print(f"   ✓  {fname} cached")
except Exception:
    print("   ⚠️  Could not download static assets — CDN fallbacks will be used")

# ── 4. Start the Flask server ────────────────────────────────────────────────
print("\n🚀  Starting AQACT server...")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def open_browser():
    time.sleep(2.5)
    import webbrowser
    webbrowser.open("http://localhost:5000")

import threading
threading.Thread(target=open_browser, daemon=True).start()

print("\n" + "=" * 60)
print("  ✅  AQACT is running!")
print("  🌐  Open your browser to: http://localhost:5000")
print()
print("  🆕  New in v2:")
print("      • Batch Stack tab — merge daily sensor files")
print("      • Multi-Station tab — compare locations side-by-side")
print("      • New sensors: Sensirion SEN5x/SPS30, Atmotube Pro,")
print("        Plantower PMS5003/7003, Generic auto-detect")
print("      • New plots: Violin, Bubble, Radar, CO2+Met, NC fractions,")
print("        Exceedance curve, PM size distribution")
print("      • Dark / Light theme toggle")
print("      • CO2 & VOC advisory levels in AQI module")
print()
print("  ⛔  Press Ctrl+C to stop the server")
print("=" * 60 + "\n")

from app import app
app.run(host="0.0.0.0", port=5000, debug=False)
