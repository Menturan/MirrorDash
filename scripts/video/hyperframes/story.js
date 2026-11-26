/* The timelines of the three videos. The scene times come from the HTML: each phone clip's data-start, data-media-start
   and data-duration, and taps.json (seconds into each recording at which a finger touches the screen). */
const TAPS = {
  "v-download": [2.88], "v-wifi": [1.87, 4.75], "v-admin": [3.41, 6.46],
  "v-modules": [1.65, 5.26, 6.59], "v-settings": [1.65, 5.74, 10.65, 15.55],
};
const el = (id) => document.getElementById(id);
const start = (id) => +el(id).dataset.start;
const end = (id) => start(id) + +el(id).dataset.duration;
/* a moment in a recording -> a moment in the film */
const at = (id, media) => start(id) + media - +(el(id).dataset.mediaStart || 0);

function story(kind) {
  const tl = gsap.timeline({ paused: true });
  const full = kind !== "loop";
  const stageIn = full ? 4.5 : 0;

  /* a caption rises in and, later, lifts away */
  const caption = (id, from, to) => {
    tl.fromTo("#" + id, { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }, from);
    if (to) tl.to("#" + id, { opacity: 0, y: -12, duration: 0.45, ease: "power2.in" }, to);
  };
  /* the phone leans in a little when a finger touches it */
  const tap = (t) => {
    tl.to("#phone", { scale: 1.04, duration: 0.35, ease: "power2.out" }, t - 0.1);
    tl.to("#phone", { scale: 1, duration: 0.9, ease: "power2.inOut" }, t + 0.3);
  };
  const screen = (id, t, d = 1.1) => tl.fromTo("#" + id, { opacity: 0 }, { opacity: 1, duration: d, ease: "power1.inOut" }, t);
  const fadeIn = (id, t, d = 0.5) => tl.fromTo("#" + id, { opacity: 0 }, { opacity: 1, duration: d, ease: "power1.inOut" }, t);
  const allTaps = (...ids) => ids.forEach((id) => TAPS[id].forEach((m) => tap(at(id, m))));

  /* the stage appears: with the opener photo it fades in over it, in the loop it fades in from black */
  tl.set("#m-boot", { opacity: 1 }, 0);
  if (full) {
    tl.set("#stage", { opacity: 0 }, 0);
    tl.fromTo("#photo-img", { scale: 1 }, { scale: 1.28, duration: stageIn + 1, ease: "power1.inOut" }, 0);
    tl.fromTo("#open-title", { opacity: 0, y: 26 }, { opacity: 1, y: 0, duration: 1.2, ease: "power3.out" }, 0.7);
    tl.fromTo("#open-sub", { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 1.1, ease: "power3.out" }, 1.3);
    tl.to(["#open-title", "#open-sub"], { opacity: 0, duration: 0.7, ease: "power1.in" }, 3.7);
    tl.to("#stage", { opacity: 1, duration: 1, ease: "power1.inOut" }, stageIn);
    tl.to("#photo", { opacity: 0, duration: 0.1 }, stageIn + 1);
    tl.fromTo("#phone", { y: 50 }, { y: 0, duration: 1.4, ease: "power3.out" }, stageIn);
  }

  if (full) {
    /* 1 Download, 2 Wi-Fi, 3 password, 4 modules: each recording fades in over the one before; the mirror follows it */
    const [dl, wifi, admin, mod] = ["v-download", "v-wifi", "v-admin", "v-modules"];
    fadeIn("v-wifi", start(wifi)); fadeIn("v-admin", start(admin)); fadeIn("v-modules", start(mod));
    screen("m-wifi", start(wifi) + 0.3); screen("m-admin", start(admin) + 0.3); screen("m-clock", start(mod) + 0.3);
    caption("c1", start(dl) + 0.3, start(wifi) - 0.1);
    caption("c2", start(wifi) + 0.3, start(admin) - 0.1);
    caption("c3", start(admin) + 0.3, start(mod) - 0.1);
    caption("c4", start(mod) + 0.3, end(mod) - 0.7);
    screen("m-full", at(mod, TAPS["v-modules"][2]) + 1.4, 1.6);
    allTaps(dl, wifi, admin, mod);
    caption("c5", end(mod) - 0.2, kind === "tour" ? end(mod) + 2.3 : el("end").dataset.at - 0.6);
    let last = end(mod);
    if (kind === "tour") {
      fadeIn("v-settings", start("v-settings"), 0.7);
      caption("c6", start("v-settings") + 0.5, end("v-settings") - 0.6);
      allTaps("v-settings");
      last = end("v-settings");
    }
    const e = +el("end").dataset.at;
    tl.to("#stage", { opacity: 0, duration: 0.8, ease: "power1.inOut" }, e - 0.2);
    tl.fromTo("#end", { opacity: 0 }, { opacity: 1, duration: 0.8, ease: "power1.inOut" }, e);
    tl.fromTo("#end .t", { y: 18 }, { y: 0, duration: 1, ease: "power3.out" }, e);
    tl.fromTo("#end .s", { y: 14 }, { y: 0, duration: 1, ease: "power3.out" }, e + 0.2);
  } else {
    /* the loop: install Weather, it fades in next to the clock, then everything fades out so the end meets the start */
    const mod = "v-modules", dur = 9.5;
    tl.set("#m-clock", { opacity: 1 }, 0);
    tl.fromTo("#stage", { opacity: 0 }, { opacity: 1, duration: 0.7, ease: "power1.inOut" }, 0);
    tl.fromTo("#phone", { y: 30 }, { y: 0, duration: 1.2, ease: "power3.out" }, 0);
    caption("c1", 0.4);
    allTaps(mod);
    screen("m-full", at(mod, TAPS["v-modules"][2]) + 1.4, 1.6);
    tl.to("#stage", { opacity: 0, duration: 0.7, ease: "power1.inOut" }, dur - 0.75);
  }
  window.__timelines[document.querySelector("[data-composition-id]").dataset.compositionId] = tl;
}
