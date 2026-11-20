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
  4. Generates the bundled demo dataset if it is missing
  5. Starts the Flask web server
  6. Opens your browser automatically

Everything runs locally: no API keys, no cloud services, no telemetry.
"""

import sys
import os
import subprocess
import time
import shutil

# Windows consoles and redirected output often default to cp1252, which cannot
# encode the emoji used below. Force UTF-8 so `python run.py > log.txt` and
# scheduled tasks behave the same as an interactive terminal.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Install the browser map package from npm for fresh checkouts ─────────────
LEAFLET_GLOBAL = os.path.join(ROOT_DIR, "node_modules", "leaflet", "dist", "leaflet.js")
LEAFLET_CSS = os.path.join(ROOT_DIR, "node_modules", "leaflet", "dist", "leaflet.css")
LEAFLET_HEAT = os.path.join(ROOT_DIR, "node_modules", "leaflet.heat", "dist", "leaflet-heat.js")
if not (os.path.isfile(LEAFLET_GLOBAL) and os.path.isfile(LEAFLET_CSS)
        and os.path.isfile(LEAFLET_HEAT)):
    if shutil.which("npm"):
        print("\n📦  Installing Leaflet and the heat plugin from npm...")
        result = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=ROOT_DIR, text=True,
        )
        if result.returncode != 0:
            print("   ⚠️  npm install failed; map assets will use the CDN fallback.")
    else:
        print("\n⚠️  npm was not found; the map will use the Leaflet CDN fallback.")

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
    "pillow",        # offline chart inspection + publication PNG/PDF export
]

IMPORT_NAMES = {
    "scikit-learn": "sklearn",
    "pillow": "PIL",
}

print("\n📦  Checking / installing required packages...")
print("    (only takes a while the very first time)\n")

for pkg in PACKAGES:
    import_name = IMPORT_NAMES.get(pkg, pkg.replace("-", "_").split(">=")[0])
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
    ("maplibre-gl-5.24.0.js",
     "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"),
    ("maplibre-gl-5.24.0.css",
     "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css"),
    ("leaflet-maplibre-gl-0.1.4.js",
     "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js"),
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

if os.path.isfile(LEAFLET_GLOBAL) and os.path.isfile(LEAFLET_CSS):
    shutil.copy2(LEAFLET_GLOBAL, os.path.join(STATIC_DIR, "leaflet-1.9.4.js"))
    shutil.copy2(LEAFLET_CSS, os.path.join(STATIC_DIR, "leaflet-1.9.4.css"))
if os.path.isfile(LEAFLET_HEAT):
    shutil.copy2(LEAFLET_HEAT, os.path.join(STATIC_DIR, "leaflet-heat.js"))
if all(os.path.isfile(path) for path in (LEAFLET_GLOBAL, LEAFLET_CSS, LEAFLET_HEAT)):
    print("   ✓  Leaflet 1.9.4 and heat plugin browser assets ready")

# ── 4. Generate the demo dataset (once) ─────────────────────────────────────
DEMO_PATH = os.path.join(ROOT_DIR, "sample_data", "demo_air_quality.csv")
DEMO_SCRIPT = os.path.join(ROOT_DIR, "sample_data", "make_demo.py")
if not os.path.exists(DEMO_PATH):
    print("\n🧪  Generating the bundled demo dataset...")
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, DEMO_SCRIPT], check=True, env=env)
        print("   ✓  Demo dataset ready")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️  Could not generate the demo dataset ({exc}).")
        print("       The rest of the app still works; upload your own file.")
else:
    print("\n🧪  Demo dataset already present")

# ── 5. Start the Flask server ────────────────────────────────────────────────
print("\n🚀  Starting AQACT server...")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = int(os.environ.get("PORT", "5000"))

def open_browser():
    time.sleep(2.5)
    import webbrowser
    webbrowser.open(f"http://localhost:{PORT}")

import threading
threading.Thread(target=open_browser, daemon=True).start()

print("\n" + "=" * 60)
print("  ✅  AQACT is running!")
print(f"  🌐  Open your browser to: http://localhost:{PORT}")
print()
print("  🆕  Highlights:")
print("      • AdminCN dashboard styling with a hover-revealed right AI panel")
print("      • Keyless AI assistant — sign in, choose a model, explain results and charts")
print("      • Publication export: PNG, SVG and print-ready PDF")
print("      • Prioritised insights, session undo and workspace restore")
print("      • Batch Stack and Multi-Station comparison")
print("      • Sensirion, Atmotube, Plantower and generic auto-detect")
print("      • Violin, Bubble, Radar, CO2+Met, exceedance and size plots")
print()
print("  🔒  All computation stays on this machine. No API keys required.")
print()
print("  ⛔  Press Ctrl+C to stop the server")
print("=" * 60 + "\n")

from app import app
app.run(host="0.0.0.0", port=PORT, debug=False)
