"""Three MirrorDash concept videos from the recorded clips: a stage with the mirror and a phone, step captions.

    compose.py <video dir> <repo dir>   ->  <video dir>/out/mirrordash-{loop,intro,tour}.mp4
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

V, REPO = Path(sys.argv[1]), Path(sys.argv[2])
CLIPS, ASSETS, SEGS, OUT = V / "clips", V / "assets", V / "segments", V / "out"
for d in (ASSETS, SEGS, OUT):
    d.mkdir(exist_ok=True)
W, H, FPS = 1920, 1080, 30
MX, MY, MW, MH = 80, 250, 1220, 738          # the mirror's glass
FR = 16                                       # its frame
PX, PY, PW, PH = 1430, 112, 390, 844          # the phone's screen (clips are recorded at this size)
FONT_L, FONT_M = V / "Inter-Light.ttf", V / "Inter-SemiBold.ttf"
if not FONT_L.exists():                       # ffmpeg's drawtext needs TTFs: two weights of the site's Inter
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    for path, weight in ((FONT_L, 300), (FONT_M, 600)):
        font = TTFont(REPO / "docs/inter-latin.woff2")
        font.flavor = None
        instantiateVariableFont(font, {"wght": weight}).save(path)


# ---------- stills ----------
def stage_bg(path, phone=True, mirror=True):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        c = round(16 - 8 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c, c + 1))
    if mirror:
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rectangle([MX - FR + 20, MY - FR + 30, MX + MW + FR - 20, MY + MH + FR + 40], fill=160)
        img = Image.composite(Image.new("RGB", (W, H)), img, sh.filter(ImageFilter.GaussianBlur(40)))
        d = ImageDraw.Draw(img)
        d.rectangle([MX - FR, MY - FR, MX + MW + FR, MY + MH + FR], fill=(36, 36, 38))
        d.rectangle([MX - FR, MY - FR, MX + MW + FR, MY - FR + 2], fill=(96, 96, 100))
        d.rectangle([MX - 2, MY - 2, MX + MW + 1, MY + MH + 1], fill=(6, 6, 7))
    if phone:
        d.rounded_rectangle([PX - 18, PY - 26, PX + PW + 18, PY + PH + 26], radius=58, fill=(24, 24, 26), outline=(70, 70, 74), width=2)
        d.rounded_rectangle([PX + PW // 2 - 50, PY - 16, PX + PW // 2 + 50, PY - 4], radius=6, fill=(10, 10, 11))
    img.save(path)


def sheen(path):
    """A faint diagonal reflection over the glass: black stays glass, the text shines through."""
    a = Image.new("L", (MW, MH), 0)
    d = ImageDraw.Draw(a)
    for i in range(MW + MH):
        t = i / (MW + MH)
        v = 22 if 0.18 < t < 0.30 else 12 if 0.34 < t < 0.37 else 10 if 0.66 < t < 0.74 else 0
        if v:
            d.line([(i, 0), (i - MH, MH)], fill=v)
    a = a.filter(ImageFilter.GaussianBlur(40))
    img = Image.new("RGBA", (MW, MH), (255, 255, 255, 0))
    img.putalpha(a)
    img.save(path)


def mirror_still(src, path, keep_right=None, center_small=False, black_below=None):
    """A screen of the mirror at the glass size; text glows a little as it does through glass."""
    if center_small:
        s = Image.open(src).convert("RGB")
        img = Image.new("RGB", (MW, MH))
        img.paste(s, ((MW - s.width) // 2, (MH - s.height) // 2))
    else:
        img = Image.open(src).convert("RGB").resize((MW, MH), Image.LANCZOS)
        if keep_right:                                        # only the clock: the rest is black glass
            ImageDraw.Draw(img).rectangle([0, 0, round(MW * keep_right), MH], fill=(0, 0, 0))
        if black_below:                                       # no calendar: only what was installed shows
            ImageDraw.Draw(img).rectangle([0, round(MH * black_below), round(MW * 0.62), MH], fill=(0, 0, 0))
    glow = img.filter(ImageFilter.GaussianBlur(7)).point(lambda v: v * 0.45)
    ImageChops.add(img, glow).save(path)


def phone_mask(path):
    m = Image.new("L", (PW, PH), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, PW - 1, PH - 1], radius=40, fill=255)
    m.save(path)


stage_bg(ASSETS / "bg.png")
stage_bg(ASSETS / "bg-card.png", phone=False, mirror=False)
sheen(ASSETS / "sheen.png")
phone_mask(ASSETS / "mask.png")
mirror_still(REPO / "mirrordash_core/static/splash.png", ASSETS / "m-boot.png", center_small=True)
mirror_still(CLIPS / "m-wifi.png", ASSETS / "m-wifi.png")
mirror_still(CLIPS / "m-admin.png", ASSETS / "m-admin.png")
mirror_still(REPO / "screenshot.png", ASSETS / "m-clock.png", keep_right=0.62)
mirror_still(REPO / "screenshot.png", ASSETS / "m-full.png", black_below=0.47)
Image.new("RGB", (MW, MH)).save(ASSETS / "m-off.png")
Image.open(REPO / "mirrordash_core/static/icon-512.png").resize((150, 150), Image.LANCZOS).save(ASSETS / "mark.png")


# ---------- segments ----------
def esc(text):
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def caption(step, text, start=0.3, size=60):
    """Step number (small, grey) over the line (large, white), top left above the mirror, fading in."""
    alpha = f"alpha='min(1,max(0,(t-{start})/0.5))'"
    parts = []
    if step:
        parts.append(f"drawtext=fontfile={FONT_M}:text='STEP {esc(step)}':fontsize=24:fontcolor=0x999999:x={MX}:y=92:{alpha}")
    parts.append(f"drawtext=fontfile={FONT_L}:text='{esc(text)}':fontsize={size}:fontcolor=white:x={MX}:y={130 + (60 - size) // 2}:{alpha}")
    return ",".join(parts)


def segment(name, dur, mirror, phone=None, step="", text="", fade_to=None, fade_at=None, size=60):
    """mirror: a still; fade_to/fade_at: a second still that fades in at fade_at s. phone: (clip, start, end)."""
    inputs = ["-loop", "1", "-t", str(dur), "-i", ASSETS / "bg.png",
              "-loop", "1", "-t", str(dur), "-i", ASSETS / mirror,
              "-loop", "1", "-t", str(dur), "-i", ASSETS / "sheen.png"]
    f = [f"[1:v]scale={MW}:{MH},format=rgba[m]"]
    n = 3
    if fade_to:
        inputs += ["-loop", "1", "-t", str(dur), "-i", ASSETS / fade_to]
        f += [f"[{n}:v]scale={MW}:{MH},format=rgba,fade=in:st={fade_at}:d=1.6:alpha=1[m2]", "[m][m2]overlay[m]"]
        n += 1
    f += ["[0:v][m]overlay=" + f"{MX}:{MY}[s]", f"[s][2:v]overlay={MX}:{MY}[s]"]
    if phone:
        clip, a, b = phone
        inputs += ["-i", CLIPS / clip, "-loop", "1", "-t", str(dur), "-i", ASSETS / "mask.png"]
        f += [f"[{n}:v]trim={a}:{b},setpts=PTS-STARTPTS,fps={FPS},scale={PW}:{PH},"
              f"tpad=stop_mode=clone:stop_duration={dur},trim=0:{dur},format=rgba[p]",
              f"[{n + 1}:v]format=gray[pm]", "[p][pm]alphamerge[p]", f"[s][p]overlay={PX}:{PY}[s]"]
    last = "[s]"
    if text:
        f.append(f"[s]{caption(step, text, size=size)}[s2]")
        last = "[s2]"
    run(inputs, f, last, dur, SEGS / f"{name}.mp4")


def card(name, dur, title, sub):
    inputs = ["-loop", "1", "-t", str(dur), "-i", ASSETS / "bg-card.png",
              "-loop", "1", "-t", str(dur), "-i", ASSETS / "mark.png"]
    f = ["[0:v][1:v]overlay=(W-w)/2:300[s]",
         f"[s]drawtext=fontfile={FONT_L}:text='{esc(title)}':fontsize=96:fontcolor=white:x=(w-text_w)/2:y=500,"
         f"drawtext=fontfile={FONT_L}:text='{esc(sub)}':fontsize=40:fontcolor=0x999999:x=(w-text_w)/2:y=640[s2]"]
    run(inputs, f, "[s2]", dur, SEGS / f"{name}.mp4")


def run(inputs, filters, last, dur, out):
    cmd = ["ffmpeg", "-v", "error", "-y", *map(str, inputs), "-filter_complex", ";".join(filters),
           "-map", last, "-t", str(dur), "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    print("segment", out.name)


def join(names, out, xfade=0.6):
    """Segments one after another, each fading into the next."""
    durs = [float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  SEGS / f"{n}.mp4"], capture_output=True, text=True).stdout) for n in names]
    inputs, f, last, offset = [], [], "[0:v]", 0.0
    for n in names:
        inputs += ["-i", SEGS / f"{n}.mp4"]
    for i in range(1, len(names)):
        offset += durs[i - 1] - xfade
        f.append(f"{last}[{i}:v]xfade=transition=fade:duration={xfade}:offset={offset:.3f}[x{i}]")
        last = f"[x{i}]"
    cmd = ["ffmpeg", "-v", "error", "-y", *map(str, inputs)]
    cmd += ["-filter_complex", ";".join(f), "-map", last] if f else ["-map", "0:v"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("video", out.name, round(sum(durs) - xfade * (len(names) - 1), 1), "s")


card("title", 3.2, "MirrorDash", "Smart mirror software for Raspberry Pi")
segment("download", 4.6, "m-boot.png", ("p-download.webm", 0.4, 2.25), "1", "Download and flash the image")
segment("wifi", 8.0, "m-wifi.png", ("p-wifi.webm", 0.3, 7.2), "2", "Join its Wi-Fi from your phone")
segment("admin", 7.4, "m-admin.png", ("p-admin.webm", 0.4, 7.4), "3", "Choose a password")
segment("modules", 9.7, "m-clock.png", ("p-modules.webm", 1.0, 10.7), "4", "Add what you want to see",
        fade_to="m-full.png", fade_at=6.6)
segment("done", 4.5, "m-full.png", ("p-modules.webm", 10.6, 10.7), "", "That’s it. Just a mirror, a little smarter.")
segment("settings", 19.6, "m-full.png", ("p-settings.webm", 0.6, 20.2), "",
        "Change anything from your phone")
card("end", 3.6, "mirrordash.net", "Free and open source")
segment("loop", 9.5, "m-clock.png", ("p-modules.webm", 3.6, 10.7), "", "Install from your phone. It shows up on the mirror.",
        fade_to="m-full.png", fade_at=4.1, size=44)

join(["loop"], OUT / "mirrordash-loop.mp4")
join(["title", "download", "wifi", "admin", "modules", "done", "end"], OUT / "mirrordash-intro.mp4")
join(["title", "download", "wifi", "admin", "modules", "done", "settings", "end"], OUT / "mirrordash-tour.mp4")
