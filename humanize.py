#!/usr/bin/env python3
"""
Human-like interaction + anti-detection helpers for the Greenhouse auto-apply tool.

Two pieces:
  1. build_fingerprint(seed)   -> a stable per-profile fingerprint dict
     apply_fingerprint(ctx, fp) -> install stealth init-scripts on a context
        (MUST be called before any page navigates -- init scripts only run on
         pages created/navigated after they're registered).

  2. human_delay()             -> a "thinking" pause before an action.
     human_type(page, locator, text) -> type like a person: variable speed,
        ~8% adjacent-key typos with backspace corrections, occasional pauses.

The fingerprint seed is derived from the profile (e.g. the email) so a given
profile always presents the SAME UA / screen / WebGL / canvas -- changing it
between runs is itself a detectable signal.
"""

import hashlib
import random
import time


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]
SCREEN_SIZES = [(1920, 1080), (1366, 768), (1440, 900), (1536, 864), (1280, 800)]
WEBGL = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("NVIDIA Corporation", "NVIDIA GeForce GTX 1650/PCIe/SSE2"),
    ("AMD", "AMD Radeon RX 5500 XT"),
]

NEIGHBORS = {
    'a': 'sqwz', 'b': 'vghn', 'c': 'xdfv', 'd': 'serfcx', 'e': 'wrsd',
    'f': 'drtgvc', 'g': 'ftyhbv', 'h': 'gyujnb', 'i': 'ujko', 'j': 'huikmn',
    'k': 'jiolm', 'l': 'kop', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
    'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'awedxz', 't': 'rfgy',
    'u': 'yhij', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu', 'z': 'asx',
}

MIN_DELAY, MAX_DELAY = 5, 12          # seconds, "thinking" gap before an action
MIN_KEY_MS, MAX_KEY_MS = 50, 200      # per-keystroke speed


def seed_from_profile(profile):
    """Stable integer seed from the profile so a fingerprint never changes.

    Prefers email; falls back to first+last name. Deterministic across runs.
    """
    basis = (profile.get("email")
             or f"{profile.get('first_name', '')}{profile.get('last_name', '')}"
             or "default")
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def build_fingerprint(seed):
    """Deterministic per-profile fingerprint from an integer seed."""
    rng = random.Random(seed)
    w, h = rng.choice(SCREEN_SIZES)
    vendor, renderer = rng.choice(WEBGL)
    return {
        "user_agent": rng.choice(USER_AGENTS),
        "viewport": {"width": w, "height": h},
        "screen": {"width": w, "height": h},
        "locale": "en-US",
        "timezone_id": "Asia/Kolkata",
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "canvas_seed": rng.randint(1, 255),
    }


def context_kwargs(fp):
    """kwargs for browser.new_context() that pin the fingerprint at the source."""
    return {
        "user_agent": fp["user_agent"],
        "viewport": fp["viewport"],
        "screen": fp["screen"],
        "locale": fp["locale"],
        "timezone_id": fp["timezone_id"],
    }


def apply_fingerprint(ctx, fp):
    """Install stealth init-scripts on a context BEFORE navigation.

    Init scripts run on every page/frame the context creates, so this must
    happen before page.goto() for the scripts to take effect on that page.
    """
    vendor = fp["webgl_vendor"]
    renderer = fp["webgl_renderer"]
    canvas_seed = fp["canvas_seed"]

    # (a) Per-profile imperceptible canvas noise -> unique-but-stable canvas hash.
    canvas_script = f"""
    (function() {{
        const seed = {canvas_seed};
        const orig = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
            const d = orig.call(this, x, y, w, h);
            for (let i = 0; i < d.data.length; i += 100) {{
                d.data[i] ^= seed & 0x1;
            }}
            return d;
        }};
    }})();
    """

    # (b) Spoof reported GPU vendor/renderer.
    webgl_script = f"""
    (function() {{
        const orig = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {{
            if (p === 37445) return {vendor!r};   // UNMASKED_VENDOR_WEBGL
            if (p === 37446) return {renderer!r}; // UNMASKED_RENDERER_WEBGL
            return orig.call(this, p);
        }};
    }})();
    """

    # (c) Hide the automation flag.
    webdriver_script = (
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    ctx.add_init_script(canvas_script)
    ctx.add_init_script(webgl_script)
    ctx.add_init_script(webdriver_script)


def human_delay():
    """A 'thinking' pause before performing an action."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def human_type(page, locator, text):
    """Type `text` into `locator` like a human.

    Variable speed, ~4% mid-word pauses, ~8% adjacent-key typos that get
    noticed a beat later and backspace-corrected. Clears the field first so
    this is a drop-in replacement for locator.fill(text).
    """
    if text is None:
        return
    text = str(text)
    try:
        locator.fill("")  # start from empty, like clicking in and selecting-all
    except Exception:
        pass

    i = 0
    while i < len(text):
        ch = text[i]
        delay = random.randint(MIN_KEY_MS, MAX_KEY_MS)

        # (a) occasional mid-typing pause
        if random.random() < 0.04:
            time.sleep(random.uniform(0.3, 0.9))

        # (b) occasional adjacent-key typo + correction
        if random.random() < 0.08 and ch.lower() in NEIGHBORS:
            wrong = random.choice(NEIGHBORS[ch.lower()])
            if ch.isupper():
                wrong = wrong.upper()

            locator.type(wrong, delay=delay)
            time.sleep(random.uniform(0.08, 0.22))

            # sometimes type a char or two more before noticing the mistake
            lookahead = random.randint(0, 2)
            extra = "".join(
                text[i + 1 + j] for j in range(lookahead) if i + 1 + j < len(text)
            )
            if extra:
                locator.type(extra, delay=delay)
                time.sleep(random.uniform(0.1, 0.3))

            for _ in range(len(extra) + 1):  # backspace extra + the wrong char
                page.keyboard.press("Backspace")
                time.sleep(random.uniform(0.06, 0.15))

            time.sleep(random.uniform(0.1, 0.35))
            locator.type(ch + extra, delay=delay)  # retype correctly
            i += 1 + len(extra)
        else:
            locator.type(ch, delay=delay)
            i += 1
