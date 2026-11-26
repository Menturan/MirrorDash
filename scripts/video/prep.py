"""The media for the HyperFrames project, and the encodes for the website.

    prep.py assets <clips dir>      clips (.webm -> .mp4), mirror stills, photo, font -> hyperframes/assets/
    prep.py encode <renders dir>    renders/{loop,intro,tour}.mp4 -> docs/video/ (the site's files)
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parents[2]
PROJECT = Path(__file__).parent / "hyperframes"
ASSETS = PROJECT / "assets"
SITE = REPO / "docs" / "video"
H264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an"]


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def assets(clips: Path):
    (ASSETS / "fonts").mkdir(parents=True, exist_ok=True)
    for webm in clips.glob("p-*.webm"):      # Playwright's variable-rate VP8 -> constant 30 fps H.264, as the renderer wants
        ffmpeg("-i", webm, "-r", 30, "-c:v", "libx264", "-crf", 12, "-preset", "slow", "-pix_fmt", "yuv420p", "-an",
               ASSETS / f"{webm.stem}.mp4")
    ffmpeg("-sseof", -0.1, "-i", clips / "p-modules.webm", "-frames:v", 1, "-update", 1, ASSETS / "p-modules-last.png")
    for still in ("m-wifi.png", "m-admin.png"):
        shutil.copy(clips / still, ASSETS / still)
    for src, name in ((REPO / "screenshot.png", "m-clock.png"), (REPO / "screenshot.png", "m-full.png"),  # two copies: one file, one use
                      (REPO / "mirrordash_core/static/splash.png", "m-boot.png"),
                      (REPO / "mirrordash_core/static/icon-512.png", "mark.png"),
                      (REPO / "docs/inter-latin.woff2", "fonts/inter.woff2")):
        shutil.copy(src, ASSETS / name)
    photo = REPO / "mirrordash_photo.jpg"    # the real photo at 2000 px, the source of docs/hallway.webp
    if photo.exists():
        shutil.copy(photo, ASSETS / "hallway.jpg")
    else:
        ffmpeg("-i", REPO / "docs/hallway.webp", ASSETS / "hallway.jpg")
    print("assets ->", ASSETS)


def encode(renders: Path):
    SITE.mkdir(exist_ok=True)
    loop = renders / "loop.mp4"
    ffmpeg("-i", loop, "-vf", "scale=1280:720:flags=lanczos", "-crf", 31, "-preset", "veryslow", *H264, SITE / "loop.mp4")
    ffmpeg("-i", loop, "-crf", 29, "-preset", "veryslow", *H264, SITE / "loop-hd.mp4")
    ffmpeg("-ss", 8.3, "-i", loop, "-frames:v", 1, "-vf", "scale=1280:720:flags=lanczos", "-q:v", 4, SITE / "loop.jpg")
    for name in ("intro", "tour"):
        ffmpeg("-i", renders / f"{name}.mp4", "-crf", 27, "-preset", "veryslow", *H264, SITE / f"{name}.mp4")
    for f in sorted(SITE.iterdir()):
        print(f.name, f.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in ("assets", "encode"):
        {"assets": assets, "encode": encode}[sys.argv[1]](Path(sys.argv[2]))
    else:
        sys.exit(__doc__)
