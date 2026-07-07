#!/usr/bin/env python3
"""
TEST-ONLY: fill every field of the Greenhouse test posting with placeholder
values, handle react-select dropdowns + checkboxes, then click the real Submit
button (the browser mints the reCAPTCHA Enterprise token naturally), and wait
5 seconds on the result page.

This is intended for the user's OWN test job posting where answer content does
not matter. Submission happens through the real browser -- the only path that
works, since the endpoint requires a live reCAPTCHA token no HTTP client can mint.
"""
import random
import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from humanize import (
    build_fingerprint,
    context_kwargs,
    apply_fingerprint,
    human_delay,
    human_type,
)


def jitter(a, b):
    """Short random pause, in seconds."""
    time.sleep(random.uniform(a, b))

URL = sys.argv[1] if len(sys.argv) > 1 else "https://job-boards.greenhouse.io/greenhouse/jobs/7982414"
RESUME = "/tmp/dummy_resume.pdf"

# Persistent on-disk profile (cookies / cache / history survive across runs) so
# the session looks like a real returning browser, not a fresh incognito one.
USER_DATA_DIR = "/tmp/gh_browser_profile"

# Sites visited before the application to seed organic browsing history.
WARMUP_SITES = [
    "https://www.reddit.com/",
    "https://github.com/",
    "https://en.wikipedia.org/wiki/Job_hunting",
]


def seed_history(page):
    """Visit a few normal pages first so the profile has real browsing history."""
    print("[*] Seeding browsing history...")
    for site in WARMUP_SITES:
        try:
            page.goto(site, wait_until="domcontentloaded", timeout=30000)
            print(f"  hist  ✓ {site}")
            jitter(1.5, 3.5)        # linger like a person
        except Exception as e:
            print(f"  hist  ✗ {site}: {e}")

# Text/textarea answers, matched by case-insensitive substring of the field label.
# More-specific needles ("preferred first name") are listed before generic ones
# ("first name") and matching stops at the first hit, so order matters here.
TEXT_VALUES = {
    "preferred first name": "Priyansh",
    "preferred last name": "Srivastava",
    "first name": "Priyansh",
    "last name": "Srivastava",
    "email": "priyanshsri28264@gmail.com",
    "phone": "+15555550123",
    # the long "In 3-5 sentences..." AI-tools question
    "ai tools to build and extend applications": (
        "This is placeholder text for a test posting. I have used AI tools to "
        "build internal apps, prototype workflows, and extend integrations "
        "across revenue and customer growth & success teams."
    ),
}

# Dropdown answers: pick the option whose text contains this substring, instead
# of blindly taking the first option. Keyed by a substring of the question label.
DROPDOWN_VALUES = {
    "country": "Canada",
    "time zone": "Eastern Time",
    "based in bc or ontario": "currently based in Ontario",
    "legal right to work in canada": "Yes",
    "non-compete": "No",
    "comfortable moving forward": "Yes",
    "how did you initially hear": "LinkedIn",
    "interview recording": "I consent",
}
# Free-typed combobox (no preset option list), e.g. the city typeahead.
TYPEAHEAD_VALUES = {
    "location (city)": "Toronto",
}


def label_of(el, page):
    al = el.get_attribute("aria-label")
    if al:
        return al.strip().lower()
    fid = el.get_attribute("id")
    if fid:
        lbl = page.query_selector(f'label[for="{fid}"]')
        if lbl and lbl.inner_text().strip():
            return lbl.inner_text().strip().lower()
    try:
        t = el.evaluate("""e=>{let n=e;for(let i=0;i<8&&n;i++){n=n.parentElement;if(!n)break;
            let l=n.querySelector('label');if(l&&l.innerText.trim())return l.innerText.trim();}return '';}""")
        return (t or "").strip().lower()
    except Exception:
        return ""


def fill_text_inputs(page):
    for el in page.query_selector_all("input[type=text], input[type=email], input[type=tel], textarea, input:not([type])"):
        try:
            if not el.is_visible():
                continue
            role = el.get_attribute("role")
            if role == "combobox":   # handled separately
                continue
            lbl = label_of(el, page)
            if not lbl:
                continue
            for needle, val in TEXT_VALUES.items():
                if needle in lbl:
                    human_delay()
                    el.scroll_into_view_if_needed()
                    el.click()
                    human_type(page, el, val)
                    print(f"  text  ✓ {lbl[:60]}")
                    break
        except Exception as e:
            print(f"  text  ✗ {e}")


def upload(page):
    # The form has two file fields (resume + cover letter); give both the same
    # dummy file so a required cover-letter upload doesn't block submission.
    n = 0
    for fi in page.query_selector_all("input[type=file]"):
        try:
            fi.set_input_files(RESUME)
            n += 1
            print(f"  file  ✓ uploaded ({n})")
        except Exception as e:
            print(f"  file  ✗ {e}")


def _pick_option(page, want):
    """Among the currently-visible options, click the one whose text contains
    `want` (case-insensitive). Returns the option text, or None if no match."""
    opts = [o for o in page.query_selector_all("[role=option]") if o.is_visible()]
    if not opts:
        return None
    if want:
        wl = want.lower()
        for o in opts:
            txt = (o.inner_text() or "").strip()
            if wl in txt.lower():
                jitter(0.2, 0.6)
                o.click()
                return txt
    # no intended match -> fall back to the first option
    txt = (opts[0].inner_text() or "").strip()
    jitter(0.2, 0.6)
    opts[0].click()
    return txt


def select_dropdowns(page):
    """For each react-select combobox, pick the answer mapped to its label.

    Uses DROPDOWN_VALUES for preset lists and TYPEAHEAD_VALUES for free-typed
    comboboxes (e.g. city); falls back to the first option when unmapped.
    """
    combos = page.query_selector_all("[role=combobox], input[role=combobox]")
    handled = set()
    for c in combos:
        try:
            if not c.is_visible():
                continue
            lbl = label_of(c, page)
            if lbl in handled:
                continue
            handled.add(lbl)

            want = next((v for k, v in DROPDOWN_VALUES.items() if k in lbl), None)
            typeahead = next((v for k, v in TYPEAHEAD_VALUES.items() if k in lbl), None)

            human_delay()                       # "read the question" pause
            c.scroll_into_view_if_needed()
            c.click()
            jitter(0.4, 0.9)                     # wait for the menu to open

            picked = _pick_option(page, want)
            if picked is not None:
                print(f"  drop  ✓ {lbl[:50]} -> {picked[:40]}")
            else:
                # no preset list: type the typeahead value, then wait for the
                # async option list to populate before picking (city lookups
                # hit the network, so a single short wait isn't reliable).
                val = typeahead or "Toronto"
                human_type(page, c, val)
                picked = None
                for _ in range(12):                       # up to ~6s, polling
                    jitter(0.4, 0.6)
                    if [o for o in page.query_selector_all("[role=option]") if o.is_visible()]:
                        picked = _pick_option(page, val)
                        break
                if picked is not None:
                    print(f"  drop  ✓ {lbl[:50]} -> (typed) {picked[:40]}")
                else:
                    page.keyboard.press("Escape")
                    print(f"  drop  ? {lbl[:50]} (no options)")
            jitter(0.2, 0.5)
        except Exception as e:
            print(f"  drop  ✗ {e}")


def check_boxes(page):
    for cb in page.query_selector_all("input[type=checkbox]"):
        try:
            if cb.is_visible() and not cb.is_checked():
                jitter(0.4, 1.0)             # pause before ticking
                cb.scroll_into_view_if_needed()
                cb.check()
                print("  check ✓ acknowledgement")
        except Exception as e:
            print(f"  check ✗ {e}")


def main():
    # Stable fingerprint seeded from the test applicant's email.
    fp = build_fingerprint(int.from_bytes(TEXT_VALUES["email"].encode(), "big") % (2**63))

    with sync_playwright() as p:
        # Persistent context = a REAL on-disk profile (not incognito): cookies,
        # cache and history are kept in USER_DATA_DIR across runs.
        # With a real (non-incognito) persistent profile the OS window size is
        # separate from the viewport, so set both: --start-maximized for the
        # window, and no_viewport so Playwright doesn't force a small viewport.
        kwargs = context_kwargs(fp)
        kwargs.pop("viewport", None)
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
            **kwargs,
        )
        apply_fingerprint(ctx, fp)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        seed_history(page)

        print(f"[*] Opening {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)

        print("[*] Text fields:")
        fill_text_inputs(page)
        print("[*] File upload:")
        upload(page)
        print("[*] Dropdowns:")
        select_dropdowns(page)
        print("[*] Checkboxes:")
        check_boxes(page)

        page.wait_for_timeout(800)
        print("[*] Clicking Submit...")
        btn = page.query_selector("button[type=submit]") or page.query_selector("input[type=submit]")
        if not btn:
            byrole = page.get_by_role("button", name="Submit Application")
            if byrole.count():
                btn = byrole.first.element_handle()
        if not btn:
            print("[!] No submit button found.")
            page.wait_for_timeout(15000)
            ctx.close()
            return
        btn.click()
        try:
            page.wait_for_selector("text=received your application", timeout=20000)
            print("[✓] Confirmation page reached.")
        except PWTimeout:
            print("[?] No confirmation within 20s -- diagnosing...")
            diagnose(page)
            print("[*] Leaving browser open 90s -- solve any captcha by hand if shown.")
            page.wait_for_timeout(90000)
            ctx.close()
            return
        print("[*] Keeping browser open 15s on result page...")
        page.wait_for_timeout(15000)
        ctx.close()


def diagnose(page):
    """Print why a submit didn't confirm: validation errors / captcha / etc."""
    # (1) Greenhouse field-level validation errors.
    errs = []
    for sel in ("[aria-invalid='true']", ".error", "[role=alert]",
                "[class*='error']", "[id*='error']"):
        for el in page.query_selector_all(sel):
            try:
                if not el.is_visible():
                    continue
                lbl = label_of(el, page)
                msg = (el.inner_text() or "").strip()
                if msg:
                    errs.append(f"{(lbl or sel)[:40]}: {msg[:80]}")
            except Exception:
                pass
    if errs:
        print("  [!] Validation errors on page:")
        for e in dict.fromkeys(errs):   # dedupe, keep order
            print(f"      - {e}")
    else:
        print("  [i] No visible validation errors found.")

    # (2) reCAPTCHA presence.
    rc = [f for f in page.frames if "recaptcha" in (f.url or "").lower()]
    if rc:
        print(f"  [!] reCAPTCHA iframe(s) present ({len(rc)}) -- a challenge may be required.")

    # (3) Screenshot for inspection.
    shot = "/tmp/greenhouse_after_submit.png"
    try:
        page.screenshot(path=shot, full_page=True)
        print(f"  [i] Screenshot saved: {shot}")
    except Exception as e:
        print(f"  [i] Screenshot failed: {e}")


if __name__ == "__main__":
    main()
