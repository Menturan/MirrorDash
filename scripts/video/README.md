# The concept videos

`docs/video/loop.mp4`, `loop-hd.mp4`, `loop.jpg`, `intro.mp4` and `tour.mp4` on the start page are made from the real
app, in four steps. Needs `uv`, Node 24 and ffmpeg.

| Step | Command | Result |
|------|---------|--------|
| Record | `uv run python scripts/video/record.py build_workspace/video/clips` | phone clips `p-*.webm`, mirror stills `m-*.png`, `taps.json` (`stage.py` runs the app on a throwaway HOME) |
| Prep | `python3 scripts/video/prep.py assets build_workspace/video/clips` | `hyperframes/assets/` (gitignored): clips as constant-rate MP4, stills, the photo, the font |
| Render | in `scripts/video/hyperframes`: `npx hyperframes@0.9.98 render -c intro.html -o renders/intro.mp4 -q high --video-frame-format png` (also `loop`, `tour`) | 1920x1080, 30 fps, no sound |
| Encode | `python3 scripts/video/prep.py encode scripts/video/hyperframes/renders` | the site's files in `docs/video/` |

`npx hyperframes@0.9.98 check` / `lint <file>.html` validate a film; `snapshot` shows single frames.

## The HyperFrames project (`hyperframes/`)

- `loop.html`, `intro.html`, `tour.html`: one film each (tour = intro plus the settings clip). Markup, the phone clips with
  their `data-start` / `data-media-start` / `data-duration`, and the captions.
- `story.js`: the GSAP timelines (captions, mirror screens, a small zoom of the phone on every tap). Scene times are read
  from the HTML; `TAPS` holds the tap times of each clip.
- `stage.css`: the stage (mirror glass with glow and sheen, phone frame, captions, end card). The mirror screens are
  masked in CSS: only the clock, or the calendar blacked out.
- `vendor/gsap-3.15.1.min.js`: GSAP, pinned.
- The opener of intro and tour is the real photo (`mirrordash_photo.jpg`, the source of `docs/hallway.webp`).

After recording again, clips can be a little shorter or longer: compare `taps.json` with `TAPS` in `story.js` and the
clips' times in the three HTML files, and look at the render.
