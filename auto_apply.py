#!/usr/bin/env python3
"""
Greenhouse auto-apply assistant.

Drives a REAL Chromium browser (Playwright) so that reCAPTCHA Enterprise and the
device fingerprint are generated naturally by the page itself -- there is no way
to forge those from a plain HTTP client, which is why a requests/curl bot cannot
work against Greenhouse's submit endpoint.

Strategy:
  1. Open the job's application form in a real browser.
  2. Fill every standard field we can confidently map from your profile.
  3. Upload resume / cover letter.
  4. Best-effort match for custom text/dropdown questions via profile.extra_fields.
  5. Decide:
       - If every REQUIRED field is filled -> (optionally) auto-submit.
       - If anything required is missing  -> stop, report, and leave the browser
         open so you can finish by hand. (Never submits a broken application.)

Usage:
  python auto_apply.py <greenhouse_job_url> --profile profile.json
  python auto_apply.py <url> --profile profile.json --no-submit   # force review
  python auto_apply.py <url> --profile profile.json --headless    # no UI (not recommended w/ captcha)
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from humanize import (
    seed_from_profile,
    build_fingerprint,
    context_kwargs,
    apply_fingerprint,
    human_delay,
    human_type,
)


# --- field label -> profile key heuristics ----------------------------------
# Greenhouse renders labels as plain text; we match case-insensitively on
# substrings so small wording differences ("First Name *", "First name") match.
STANDARD_MAP = [
    (("first name",), "first_name"),
    (("last name", "surname"), "last_name"),
    (("email",), "email"),
    (("phone", "mobile"), "phone"),
    (("linkedin",), ("links", "linkedin")),
    (("github",), ("links", "github")),
    (("portfolio",), ("links", "portfolio")),
    (("website", "personal site"), ("links", "website")),
    (("location", "city",), "location"),
]


def get_profile_value(profile, key):
    """key is either a string top-level key or a (parent, child) tuple."""
    if isinstance(key, tuple):
        return (profile.get(key[0]) or {}).get(key[1], "")
    return profile.get(key, "")


def load_profile(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"[!] Profile not found: {path}\n    Copy profile.example.json and edit it.")
    profile = json.loads(p.read_text())
    # validate required-to-run essentials
    for req in ("first_name", "last_name", "email"):
        if not profile.get(req):
            sys.exit(f"[!] profile.{req} is required and empty.")
    rp = profile.get("resume_path", "")
    if rp and not Path(rp).exists():
        sys.exit(f"[!] resume_path points to a missing file: {rp}")
    return profile


def text_for(handle):
    """Get the visible label text associated with an input, best-effort."""
    try:
        # Greenhouse wraps fields; the nearest label or aria-label is what we want.
        label = handle.get_attribute("aria-label") or ""
        if label:
            return label.strip().lower()
        fid = handle.get_attribute("id")
        if fid:
            lbl = handle.page.query_selector(f'label[for="{fid}"]')
            if lbl:
                return (lbl.inner_text() or "").strip().lower()
    except Exception:
        pass
    return ""


def fill_standard_fields(page, profile, report):
    inputs = page.query_selector_all('input[type="text"], input[type="email"], input[type="tel"], input:not([type])')
    for inp in inputs:
        if not inp.is_visible():
            continue
        label = text_for(inp)
        if not label:
            continue
        for needles, key in STANDARD_MAP:
            if any(n in label for n in needles):
                val = get_profile_value(profile, key)
                if val:
                    try:
                        human_delay()
                        inp.scroll_into_view_if_needed()
                        inp.click()
                        human_type(page, inp, val)
                        report["filled"].append(label)
                    except Exception as e:
                        report["errors"].append(f"{label}: {e}")
                break


def upload_files(page, profile, report):
    file_inputs = page.query_selector_all('input[type="file"]')
    resume = profile.get("resume_path", "")
    cover = profile.get("cover_letter_path", "")
    for fi in file_inputs:
        label = text_for(fi) or (fi.get_attribute("name") or "").lower()
        target = None
        if "resume" in label or "cv" in label:
            target = resume
        elif "cover" in label:
            target = cover
        elif resume and not report["resume_uploaded"]:
            target = resume  # fall back: first file field gets the resume
        if target and Path(target).exists():
            try:
                fi.set_input_files(target)
                report["filled"].append(f"upload:{label or 'file'}")
                if target == resume:
                    report["resume_uploaded"] = True
            except Exception as e:
                report["errors"].append(f"upload {label}: {e}")


def fill_extra_fields(page, profile, report):
    """Best-effort fill of custom questions from profile.extra_fields by label substring."""
    extra = profile.get("extra_fields", {}) or {}
    if not extra:
        return
    # textareas + remaining text inputs
    for el in page.query_selector_all("textarea, input[type='text']"):
        if not el.is_visible():
            continue
        label = text_for(el)
        if not label:
            continue
        for q, ans in extra.items():
            if q.strip().lower() in label and ans:
                try:
                    human_delay()
                    el.scroll_into_view_if_needed()
                    el.click()
                    human_type(page, el, ans)
                    report["filled"].append(f"extra:{label}")
                except Exception:
                    pass
                break


def find_missing_required(page):
    """Return labels of required fields that are still empty."""
    missing = []
    candidates = page.query_selector_all(
        "input[required], select[required], textarea[required], "
        "input[aria-required='true'], select[aria-required='true'], textarea[aria-required='true']"
    )
    for el in candidates:
        try:
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                val = el.evaluate("e => e.value")
            elif el.get_attribute("type") == "file":
                val = el.evaluate("e => e.files && e.files.length ? 'has' : ''")
            else:
                val = (el.get_attribute("value") or el.evaluate("e => e.value") or "")
            if not str(val).strip():
                missing.append(text_for(el) or "(unlabeled required field)")
        except Exception:
            continue
    return missing


def main():
    ap = argparse.ArgumentParser(description="Greenhouse auto-apply assistant")
    ap.add_argument("url", help="Greenhouse job application URL")
    ap.add_argument("--profile", default="profile.json", help="Path to profile JSON")
    ap.add_argument("--no-submit", action="store_true", help="Fill only; never click submit")
    ap.add_argument("--headless", action="store_true", help="Run without visible browser (captcha may fail)")
    ap.add_argument("--timeout", type=int, default=45, help="Page load timeout seconds")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    report = {"filled": [], "errors": [], "resume_uploaded": False}

    # Stable per-profile fingerprint (UA / screen / WebGL / canvas / timezone).
    fp = build_fingerprint(seed_from_profile(profile))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        # Pin the fingerprint at context creation, then install stealth
        # init-scripts BEFORE the first navigation so they apply to this page.
        ctx = browser.new_context(**context_kwargs(fp))
        apply_fingerprint(ctx, fp)
        page = ctx.new_page()

        print(f"[*] Opening {args.url}")
        try:
            page.goto(args.url, timeout=args.timeout * 1000, wait_until="domcontentloaded")
        except PWTimeout:
            print("[!] Page load timed out.")
            browser.close()
            sys.exit(1)

        # Greenhouse application forms sometimes load in an iframe (embedded boards).
        # Try the main frame first; the query helpers operate on `page` which
        # already targets the top frame. For embedded boards, navigate to the
        # canonical job-boards URL instead of the embed.
        page.wait_for_timeout(2000)

        print("[*] Filling standard fields...")
        fill_standard_fields(page, profile, report)
        print("[*] Uploading files...")
        upload_files(page, profile, report)
        print("[*] Filling custom questions (best effort)...")
        fill_extra_fields(page, profile, report)

        page.wait_for_timeout(500)
        missing = find_missing_required(page)

        print("\n--- Summary ---")
        print(f"Filled ({len(report['filled'])}): " + ", ".join(report["filled"]) or "  none")
        if report["errors"]:
            print("Errors:")
            for e in report["errors"]:
                print(f"  - {e}")
        if missing:
            print(f"\n[!] {len(missing)} required field(s) still empty:")
            for m in missing:
                print(f"  - {m}")

        should_submit = (not args.no_submit) and (not missing)

        if should_submit:
            print("\n[*] All required fields filled. Submitting...")
            # reCAPTCHA Enterprise runs invisibly on submit; a real browser
            # generates the token. Click the submit button.
            btn = page.query_selector("button[type='submit']") or page.query_selector("input[type='submit']")
            if not btn:
                by_role = page.get_by_role("button", name="Submit Application")
                if by_role.count():
                    btn = by_role.first.element_handle()
            if not btn:
                print("[!] Couldn't locate a submit button. Leaving browser open for manual submit.")
            else:
                btn.click()
                # Wait for the confirmation ("We've received your application.")
                try:
                    page.wait_for_selector("text=received your application", timeout=20000)
                    print("[✓] Application submitted -- confirmation page reached.")
                except PWTimeout:
                    print("[?] Submitted but no confirmation detected within 20s. "
                          "Check the browser -- it may be a captcha challenge or a validation error.")
        else:
            if missing:
                print("\n[i] Not auto-submitting: required fields are missing (see above).")
            else:
                print("\n[i] --no-submit set: form filled, not submitting.")
            print("    The browser stays open. Finish manually, then press Enter here to close.")

        if not args.headless:
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            time.sleep(2)
        browser.close()


if __name__ == "__main__":
    main()
