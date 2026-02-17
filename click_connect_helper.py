#!/usr/bin/env python3
"""Safe helper for manual-assisted PMI Community Connections clicking."""

import argparse
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Tuple
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


@dataclass
class Config:
    start_url: str | None
    button_label: str
    timeout_seconds: int
    user_data_dir: Path
    screenshot_dir: Path
    log_file: Path
    min_delay_seconds: float
    max_delay_seconds: float
    use_open_page: bool
    browser_channel: str


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description=(
            "Safe Playwright helper that clicks one connection button at a time "
            "with explicit user confirmation."
        )
    )
    parser.add_argument(
        "--start-url",
        help="Starting PMI connections URL. Optional when --use-open-page is set.",
    )
    parser.add_argument(
        "--button-label",
        default="Connect",
        help="Accessible button name to click exactly (default: Connect).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=15,
        help="Maximum seconds to wait for post-click UI change (default: 15).",
    )
    parser.add_argument(
        "--user-data-dir",
        default="./user_data",
        help="Persistent Playwright profile directory (default: ./user_data).",
    )
    parser.add_argument(
        "--screenshot-dir",
        default="./screenshots",
        help="Directory for click screenshots (default: ./screenshots).",
    )
    parser.add_argument(
        "--log-file",
        default="./logs/run.log",
        help="Log file path (default: ./logs/run.log).",
    )
    parser.add_argument(
        "--min-delay-seconds",
        type=float,
        default=2.0,
        help="Minimum delay after each click (default: 2.0).",
    )
    parser.add_argument(
        "--max-delay-seconds",
        type=float,
        default=4.0,
        help="Maximum delay after each click (default: 4.0).",
    )
    parser.add_argument(
        "--use-open-page",
        action="store_true",
        help=(
            "Use an already-open tab from the persistent profile instead of navigating "
            "to --start-url."
        ),
    )
    parser.add_argument(
        "--browser-channel",
        choices=["chromium", "chrome", "msedge"],
        default="chromium",
        help=(
            "Browser channel for persistent context. "
            "'chromium' uses bundled Playwright Chromium (default)."
        ),
    )

    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0.")
    if args.min_delay_seconds < 0 or args.max_delay_seconds < 0:
        parser.error("Delay values must be non-negative.")
    if args.max_delay_seconds < args.min_delay_seconds:
        parser.error("--max-delay-seconds must be >= --min-delay-seconds.")
    if not args.use_open_page and not args.start_url:
        parser.error("Provide --start-url, or use --use-open-page.")

    return Config(
        start_url=args.start_url,
        button_label=args.button_label,
        timeout_seconds=args.timeout_seconds,
        user_data_dir=Path(args.user_data_dir),
        screenshot_dir=Path(args.screenshot_dir),
        log_file=Path(args.log_file),
        min_delay_seconds=args.min_delay_seconds,
        max_delay_seconds=args.max_delay_seconds,
        use_open_page=args.use_open_page,
        browser_channel=args.browser_channel,
    )


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def ask_user(prompt: str) -> str:
    return input(prompt).strip().lower()


def is_onedrive_path(path: Path) -> bool:
    return "onedrive" in str(path).lower()


def launch_context_with_fallback(pw, cfg: Config):
    attempted = []
    channels = [cfg.browser_channel]
    if cfg.browser_channel != "chromium":
        channels.append("chromium")

    profile_dirs = [cfg.user_data_dir]
    fresh_dir = cfg.user_data_dir.parent / f"{cfg.user_data_dir.name}_fresh"
    profile_dirs.append(fresh_dir)

    last_error = None
    for profile_dir in profile_dirs:
        profile_dir.mkdir(parents=True, exist_ok=True)
        for channel in channels:
            launch_kwargs = {
                "user_data_dir": str(profile_dir),
                "headless": False,
            }
            if channel != "chromium":
                launch_kwargs["channel"] = channel
            try:
                context = pw.chromium.launch_persistent_context(**launch_kwargs)
                if profile_dir != cfg.user_data_dir:
                    logging.warning(
                        "Using fallback profile directory: %s",
                        profile_dir,
                    )
                if channel != cfg.browser_channel:
                    logging.warning(
                        "Fell back from '%s' to '%s' browser channel.",
                        cfg.browser_channel,
                        channel,
                    )
                return context
            except PlaywrightError as exc:
                attempted.append(f"channel={channel}, profile={profile_dir}")
                last_error = exc
                logging.warning(
                    "Launch failed for channel=%s profile=%s: %s",
                    channel,
                    profile_dir,
                    exc,
                )

    attempted_text = "; ".join(attempted)
    raise RuntimeError(
        "Failed to launch browser in persistent mode. "
        f"Attempted: {attempted_text}. "
        "Close all Chrome/Edge windows and try again, or choose a different --user-data-dir."
    ) from last_error


def select_initial_page(context, cfg: Config) -> Page:
    open_pages = [p for p in context.pages if not p.is_closed()]
    if cfg.use_open_page:
        if open_pages:
            print("Open tabs:")
            for idx, p in enumerate(open_pages, start=1):
                current_url = p.url or "(blank)"
                print(f"  {idx}. {current_url}")
            while True:
                choice = ask_user(
                    "Select tab number to use, or press Enter to use tab 1 (q to quit): "
                )
                if choice == "q":
                    raise KeyboardInterrupt
                if choice == "":
                    return open_pages[0]
                if choice.isdigit():
                    selected_index = int(choice)
                    if 1 <= selected_index <= len(open_pages):
                        return open_pages[selected_index - 1]
                print("Invalid selection. Try again.")

        page = context.new_page()
        answer = ask_user(
            "No open tabs found. Navigate manually in the opened browser, then press Enter to continue (or 'q' to quit): "
        )
        if answer == "q":
            raise KeyboardInterrupt
        return page

    return open_pages[0] if open_pages else context.new_page()


def wait_for_manual_verification_if_needed(page: Page) -> bool:
    captcha_selectors = [
        "iframe[title*='captcha' i]",
        "iframe[src*='captcha' i]",
        "[id*='captcha' i]",
        "[class*='captcha' i]",
    ]

    verification_detected = False
    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                verification_detected = True
                break
        except Exception:
            continue

    if not verification_detected:
        try:
            verify_text = page.get_by_text(re.compile(r"verify|verification|captcha", re.I))
            verification_detected = verify_text.count() > 0
        except Exception:
            verification_detected = False

    if not verification_detected:
        return True

    logging.warning("Possible CAPTCHA/verification detected.")
    answer = ask_user(
        "Verification/CAPTCHA may be present. Solve it manually, then press Enter to continue (or 'q' to quit): "
    )
    if answer == "q":
        logging.info("User chose to quit during verification pause.")
        return False
    return True


def wait_for_manual_recovery_if_site_error(page: Page) -> bool:
    try:
        has_error_text = page.get_by_text("Something unexpected happened", exact=False).count() > 0
    except Exception:
        has_error_text = False

    if not has_error_text:
        return True

    logging.warning("Detected PMI error page ('Something unexpected happened').")
    answer = ask_user(
        "PMI error page detected. Refresh or navigate manually, then press Enter to continue (or 'q' to quit): "
    )
    if answer == "q":
        logging.info("User chose to quit during PMI error-page recovery.")
        return False
    return True


def get_connect_button_count(page: Page, button_label: str) -> int:
    return page.get_by_role("button", name=button_label, exact=True).count()


def save_screenshot(page: Page, screenshot_dir: Path) -> Path:
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = screenshot_dir / f"click_{timestamp}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def wait_for_ui_change(
    page: Page,
    button_label: str,
    timeout_seconds: int,
    previous_connect_count: int,
    previous_connected_count: int,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        current_count = get_connect_button_count(page, button_label)
        connected_now = page.get_by_role("button", name="Connected", exact=True).count()
        if current_count < previous_connect_count or connected_now > previous_connected_count:
            return True
        page.wait_for_timeout(500)
    return False


def increment_page_query(url: str) -> Tuple[str, int]:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    current_page_str = query.get("page", ["1"])[0]
    try:
        current_page = int(current_page_str)
    except ValueError:
        current_page = 1
    next_page = current_page + 1
    query["page"] = [str(next_page)]
    next_query = urlencode(query, doseq=True)
    next_url = urlunsplit((parts.scheme, parts.netloc, parts.path, next_query, parts.fragment))
    return next_url, next_page


def click_one_connect(page: Page, cfg: Config) -> bool:
    buttons = page.get_by_role("button", name=cfg.button_label, exact=True)
    count_before = buttons.count()
    if count_before == 0:
        return False

    connected_before = page.get_by_role("button", name="Connected", exact=True).count()
    buttons.first.scroll_into_view_if_needed(timeout=5_000)
    buttons.first.click(timeout=10_000)
    screenshot_path = save_screenshot(page, cfg.screenshot_dir)
    logging.info("Clicked one '%s' button. Screenshot: %s", cfg.button_label, screenshot_path)

    changed = wait_for_ui_change(
        page,
        cfg.button_label,
        cfg.timeout_seconds,
        count_before,
        connected_before,
    )
    if changed:
        logging.info("Detected post-click UI change.")
    else:
        logging.warning(
            "No post-click UI change detected within %s seconds.",
            cfg.timeout_seconds,
        )

    delay = random.uniform(cfg.min_delay_seconds, cfg.max_delay_seconds)
    logging.info("Sleeping %.2f seconds before continuing.", delay)
    page.wait_for_timeout(int(delay * 1000))
    return True


def run(cfg: Config) -> None:
    cfg.user_data_dir.mkdir(parents=True, exist_ok=True)
    cfg.screenshot_dir.mkdir(parents=True, exist_ok=True)
    if is_onedrive_path(cfg.user_data_dir):
        logging.warning(
            "user_data_dir is inside OneDrive (%s). "
            "If browser launch is unstable, try a local path like "
            "'%LOCALAPPDATA%\\pmi_playwright_profile'.",
            cfg.user_data_dir,
        )

    with sync_playwright() as pw:
        context = launch_context_with_fallback(pw, cfg)

        try:
            page = select_initial_page(context, cfg)
            if cfg.use_open_page:
                logging.info("Using selected open page: %s", page.url)
            else:
                logging.info("Opening start URL: %s", cfg.start_url)
                page.goto(cfg.start_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

            while True:
                if page.is_closed():
                    logging.warning("Active page was closed. Ending run.")
                    break

                if not wait_for_manual_recovery_if_site_error(page):
                    break

                if not wait_for_manual_verification_if_needed(page):
                    break

                connect_count = get_connect_button_count(page, cfg.button_label)
                logging.info("Found %s '%s' button(s) on this page.", connect_count, cfg.button_label)
                print(f"Found {connect_count} '{cfg.button_label}' button(s) on this page.")

                while connect_count > 0:
                    answer = ask_user(
                        f"Press Enter to click ONE '{cfg.button_label}' button, or type 'q' to quit: "
                    )
                    if answer == "q":
                        logging.info("User requested quit.")
                        return

                    if page.is_closed():
                        logging.warning("Page was closed by user. Ending run.")
                        return

                    if not wait_for_manual_recovery_if_site_error(page):
                        return

                    if not wait_for_manual_verification_if_needed(page):
                        return

                    try:
                        clicked = click_one_connect(page, cfg)
                    except PlaywrightTimeoutError as exc:
                        logging.exception("Playwright timeout while clicking: %s", exc)
                        retry_answer = ask_user("Click timed out. Press Enter to retry or 'q' to quit: ")
                        if retry_answer == "q":
                            return
                        connect_count = get_connect_button_count(page, cfg.button_label)
                        continue

                    if not clicked:
                        logging.info("No '%s' buttons remain after re-query.", cfg.button_label)
                        break

                    connect_count = get_connect_button_count(page, cfg.button_label)
                    logging.info(
                        "Remaining '%s' buttons after click: %s",
                        cfg.button_label,
                        connect_count,
                    )

                next_page_answer = ask_user("No more 'Connect' buttons. Go to next page? (y/n): ")
                if next_page_answer != "y":
                    logging.info("User ended run after current page.")
                    break

                next_url, next_page_number = increment_page_query(page.url)
                logging.info("Navigating to next page (%s): %s", next_page_number, next_url)
                page.goto(next_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

        except KeyboardInterrupt:
            logging.info("User requested quit before run loop started.")
        finally:
            logging.info("Closing browser context.")
            try:
                context.close()
            except PlaywrightError as exc:
                # Some Playwright versions do not expose TargetClosedError in sync_api.
                if "Target page, context or browser has been closed" in str(exc):
                    logging.info("Browser context was already closed.")
                else:
                    raise


def main() -> None:
    cfg = parse_args()
    setup_logging(cfg.log_file)
    logging.info("Starting safe connect helper.")
    run(cfg)
    logging.info("Helper finished.")


if __name__ == "__main__":
    main()
