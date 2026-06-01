#!/usr/bin/env python3
"""
Run once: python3 make_app.py
Creates SMC Suite.app on your Desktop with a custom icon.
"""
import os, shutil, subprocess, sys
from pathlib import Path

HERE  = Path(__file__).parent
DESK  = Path.home() / "Desktop"
APP   = DESK / "SMC Suite.app"
ICON_SRC = HERE / "smc_backtest" / "data" / "AppIcon.png"  # we'll generate this

# ── 1. Generate icon PNG via Pillow ──────────────────────────────────────────
def make_icon(out_path):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "pillow", "--break-system-packages", "-q"])
        from PIL import Image, ImageDraw, ImageFont

    SIZE = 1024
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded background
    def rrect(d, xy, r, fill):
        x0,y0,x1,y1 = xy
        d.rectangle([x0+r,y0,x1-r,y1], fill=fill)
        d.rectangle([x0,y0+r,x1,y1-r], fill=fill)
        for ex,ey in [(x0,y0),(x1-2*r,y0),(x0,y1-2*r),(x1-2*r,y1-2*r)]:
            d.ellipse([ex,ey,ex+2*r,ey+2*r], fill=fill)

    rrect(draw, (0,0,SIZE,SIZE), 180, (13,15,20,255))

    # Grid
    gc = (42,47,63,100)
    for i in range(1,8):
        draw.line([(SIZE*i//8,80),(SIZE*i//8,SIZE-80)], fill=gc, width=1)
        draw.line([(80,SIZE*i//8),(SIZE-80,SIZE*i//8)], fill=gc, width=1)

    # Candlesticks
    candles = [
        (200,580,480,440,620,True),(290,480,540,450,580,False),
        (380,540,380,340,560,True),(470,380,280,240,400,True),
        (560,280,360,260,400,False),(650,360,220,180,380,True),
        (740,220,160,130,240,True),(830,160,260,140,290,False),
    ]
    GREEN = (34,197,94,255); RED = (239,68,68,255)
    for cx,op,cl,hi,lo,bull in candles:
        col = GREEN if bull else RED
        draw.line([(cx,hi),(cx,lo)], fill=col, width=3)
        draw.rectangle([cx-28,min(op,cl),cx+28,max(op,cl)], fill=col)

    # Signal line
    pts = [(cx,int((op+cl)/2)) for cx,op,cl,hi,lo,bull in candles]
    for i in range(len(pts)-1):
        draw.line([pts[i],pts[i+1]], fill=(59,130,246,200), width=3)
    for x,y in pts:
        draw.ellipse([x-6,y-6,x+6,y+6], fill=(59,130,246,255))

    # Accent bar
    draw.rectangle([80,72,SIZE-80,76], fill=(59,130,246,180))

    # Text — use system fonts if custom not available
    def load_font(name, size):
        paths = [
            HERE / "smc_backtest" / "fonts" / name,
            Path("/Library/Fonts") / name,
            Path("/System/Library/Fonts") / name,
        ]
        for p in paths:
            if p.exists():
                return ImageFont.truetype(str(p), size)
        return ImageFont.load_default()

    try:
        from PIL import ImageFont
        font_big = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
        font_sm  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
    except:
        font_big = ImageFont.load_default()
        font_sm  = font_big

    for label, font, y, fill in [
        ("SMC SUITE", font_big, SIZE-175, (226,232,240,255)),
        ("TRADING · ANALYSIS · JOURNAL", font_sm, SIZE-100, (100,116,139,200)),
    ]:
        bb = draw.textbbox((0,0), label, font=font)
        tw = bb[2]-bb[0]
        draw.text(((SIZE-tw)//2, y), label, font=font, fill=fill)

    img.save(str(out_path), "PNG")
    print(f"  Icon saved → {out_path}")

# ── 2. Build .iconset and .icns ───────────────────────────────────────────────
def make_icns(png_path, icns_path):
    iconset = icns_path.with_suffix(".iconset")
    iconset.mkdir(exist_ok=True)
    sizes = [
        (16,"16x16"),(32,"16x16@2x"),(32,"32x32"),(64,"32x32@2x"),
        (128,"128x128"),(256,"128x128@2x"),(256,"256x256"),(512,"256x256@2x"),
        (512,"512x512"),(1024,"512x512@2x"),
    ]
    for px, name in sizes:
        subprocess.run(
            ["sips","-z",str(px),str(px),str(png_path),"--out",str(iconset/f"icon_{name}.png")],
            capture_output=True
        )
    subprocess.run(["iconutil","-c","icns",str(iconset),"-o",str(icns_path)], check=True)
    shutil.rmtree(iconset)
    print(f"  ICNS saved → {icns_path}")

# ── 3. Build .app bundle ──────────────────────────────────────────────────────
def make_app(app_path, icns_path):
    if app_path.exists():
        shutil.rmtree(app_path)
    macos = app_path / "Contents" / "MacOS"
    res   = app_path / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    exe = macos / "SMCSuite"
    exe.write_text(
        "#!/bin/bash\n"
        "cd ~/Desktop/forex-news-dashboard\n"
        "python3 start.py\n"
    )
    exe.chmod(0o755)

    (app_path / "Contents" / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>SMC Suite</string>
    <key>CFBundleDisplayName</key><string>SMC Suite</string>
    <key>CFBundleIdentifier</key><string>com.dasha.smcsuite</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>SMCSuite</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>""")

    shutil.copy(icns_path, res / "AppIcon.icns")
    print(f"  App created → {app_path}")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tmp = Path("/tmp/smc_suite_icon.png")
    icns = Path("/tmp/SMCSuite.icns")

    print("\n  Building SMC Suite.app …")
    make_icon(tmp)
    make_icns(tmp, icns)
    make_app(APP, icns)

    # Refresh Finder icon cache
    subprocess.run(["touch", str(APP)], capture_output=True)
    print(f"\n  ✅ Done! Double-click SMC Suite on your Desktop to launch.\n")
