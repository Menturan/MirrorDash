// What does not fit in a card (style.css "Card size", ARCHITECTURE #67). Shared by the mirror (core.js) and the
// admin preview (module_preview.html), so both behave the same. watchOverflow(card) marks the card
// data-overflowing while its content is taller than it, and then, when its data-overflow is "scroll" (the
// default; "fade" only fades the bottom out), scrolls it: the top for PAUSE ms, down at SPEED px/s to the
// end, PAUSE ms there, a dip to invisible, back at the top, again. Not with reduced motion (it fades then),
// not on the layers, not while the card is hidden or an inactive carousel slide.
//
// The card itself scrolls (overflow: hidden, scrollTop): the content is the module's own elements in the
// shadow root, so there is nothing of ours to transform, and a scroll leaves the layout alone (no relayout
// per frame, which a Raspberry Pi 3 would feel). Frames only while it moves; the pauses are timers.
const OVERFLOW_PAUSE = 6000;  // ms at the top and at the end
const OVERFLOW_SPEED = 20;    // px per second
const OVERFLOW_DIP = 400;     // ms to fade out, and the same again to come back (style.css scroll-dip lasts twice this)

// For the card's shadow root, after the module's styles: while a card scrolls, its heading stays on top,
// opaque so the rows do not show through. The negative margin and the padding put its text where it was:
// sticky counts from the card's edge, not from inside its padding.
const OVERFLOW_CSS = `
:host([data-overflow="scroll"][data-overflowing]) .module-header {
    position: sticky; top: 0; z-index: 1;
    margin-top: calc(-1 * var(--internal-padding)); padding-top: var(--internal-padding);
    background: #000;
}`;

const overflowWatchers = new WeakMap();  // card -> { observer, stop: ends the scrolling, or null }

function shouldScroll(card) {
    return card.dataset.overflow !== 'fade' && card.hasAttribute('data-overflowing') && card.isConnected
        && !card.closest('.layer') && !matchMedia('(prefers-reduced-motion: reduce)').matches
        && card.getClientRects().length > 0 && !card.matches('.carousel-slide:not(.carousel-active)');
}

function startScrolling(card) {
    let timer = 0, frame = 0, position = 0;
    const maxScroll = () => card.scrollHeight - card.clientHeight;
    const reset = () => {
        position = 0;
        card.scrollTop = 0;
        card.removeAttribute('data-scroll-end');
    };
    // Each step first asks whether it still should, so a hidden card or a changed setting ends it here
    const next = (ms, step) => { timer = setTimeout(() => (shouldScroll(card) ? step() : stop()), ms); };
    const atTop = () => { card.removeAttribute('data-scroll-dip'); next(OVERFLOW_PAUSE, move); };
    const move = () => {
        let last;
        const frameStep = now => {
            position = Math.min(maxScroll(), position + (OVERFLOW_SPEED * (now - (last ?? now))) / 1000);
            last = now;
            card.scrollTop = position;
            if (position >= maxScroll() - 0.5) {
                card.setAttribute('data-scroll-end', '');  // nothing more below: no fade
                next(OVERFLOW_PAUSE, dip);
            } else {
                frame = requestAnimationFrame(frameStep);
            }
        };
        frame = requestAnimationFrame(frameStep);
    };
    const dip = () => {
        card.setAttribute('data-scroll-dip', '');
        next(OVERFLOW_DIP, () => { reset(); next(OVERFLOW_DIP, atTop); });  // invisible in the middle: back to the top unseen
    };
    const stop = () => {
        clearTimeout(timer);
        cancelAnimationFrame(frame);
        card.removeAttribute('data-scroll-dip');
        reset();
        const watcher = overflowWatchers.get(card);
        if (watcher) watcher.stop = null;
    };
    next(OVERFLOW_PAUSE, move);
    return stop;
}

// Watched again after each redraw: the card and what is in its shadow root change size as the content does.
// A scroll under way goes on (one per card, never stacked): a module that redraws every second would
// otherwise never get past its first rows.
function watchOverflow(card) {
    const watcher = overflowWatchers.get(card) ?? {};
    watcher.observer?.disconnect();
    const update = () => {
        card.toggleAttribute('data-overflowing', card.scrollHeight > card.clientHeight + 1);
        const scrolls = shouldScroll(card);
        if (scrolls && !watcher.stop) watcher.stop = startScrolling(card);
        else if (!scrolls && watcher.stop) watcher.stop();
    };
    watcher.observer = new ResizeObserver(update);
    watcher.observer.observe(card);
    for (const part of card.shadowRoot?.children ?? []) if (!['STYLE', 'SCRIPT'].includes(part.tagName)) watcher.observer.observe(part);
    if (!watcher.mutations) {  // a carousel turning, a card hidden or shown: no resize says so
        watcher.mutations = new MutationObserver(update);
        watcher.mutations.observe(card, { attributes: true, attributeFilter: ['class', 'hidden', 'data-overflow'] });
    }
    overflowWatchers.set(card, watcher);
}
