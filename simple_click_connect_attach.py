#!/usr/bin/env python3
"""Simple Connect button clicker for an already-open Chrome page via CDP."""

import argparse
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Attach to Chrome and click all visible Connect buttons."
    )
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome remote debugging URL (default: http://127.0.0.1:9222).",
    )
    parser.add_argument(
        "--button-label",
        default="Connect",
        help="Exact accessible name of button to click (default: Connect).",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.0,
        help="Delay between clicks (default: 2.0).",
    )
    parser.add_argument(
        "--max-clicks",
        type=int,
        default=0,
        help="Stop after N clicks (0 means no limit).",
    )
    parser.add_argument(
        "--url-contains",
        default="",
        help="Optional substring to pick the correct tab URL.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Stop after N pages (0 means no limit).",
    )
    parser.add_argument(
        "--send-request-label",
        default="Send Request",
        help="Exact accessible name for modal submit button (default: Send Request).",
    )
    parser.add_argument(
        "--modal-timeout-seconds",
        type=float,
        default=8.0,
        help="Wait timeout for invite modal button (default: 8.0).",
    )
    parser.add_argument(
        "--navigation-timeout-seconds",
        type=float,
        default=15.0,
        help="Timeout when moving to next page (default: 15.0).",
    )
    parser.add_argument(
        "--navigation-retries",
        type=int,
        default=3,
        help="Retries for page navigation before giving up (default: 3).",
    )
    parser.add_argument(
        "--page-settle-seconds",
        type=float,
        default=6.0,
        help="Wait/probe time before declaring a page has no Connect buttons (default: 6.0).",
    )
    parser.add_argument(
        "--no-auto-next-page",
        action="store_true",
        help="Disable automatic pagination after current page is complete.",
    )
    return parser.parse_args()


def pick_page(browser, url_contains: str):
    pages = []
    for context in browser.contexts:
        pages.extend([p for p in context.pages if not p.is_closed()])

    if not pages:
        return None

    if url_contains:
        for page in pages:
            if url_contains.lower() in (page.url or "").lower():
                return page

    for page in pages:
        if page.url and page.url != "about:blank":
            return page

    return pages[0]


def get_or_assign_button_id(button, id_seq: int) -> tuple[str, int]:
    existing = button.get_attribute("data-pmi-helper-id")
    if existing:
        return existing, id_seq
    button_id = f"pmi-helper-{int(time.time() * 1000)}-{id_seq}"
    button.evaluate(
        "(el, value) => el.setAttribute('data-pmi-helper-id', value)",
        button_id,
    )
    return button_id, id_seq + 1


def has_modal_error(page) -> bool:
    dialog = page.get_by_role("dialog").first
    if dialog.count() == 0:
        return False

    error_patterns = [
        "Cannot read properties of undefined",
        "Something went wrong",
        "error",
    ]
    for pattern in error_patterns:
        try:
            if dialog.get_by_text(pattern, exact=False).count() > 0:
                return True
        except PlaywrightError:
            continue
    return False


def is_dialog_open(page) -> bool:
    try:
        return page.get_by_role("dialog").count() > 0
    except PlaywrightError:
        return False


def close_modal_if_open(page) -> bool:
    dialog = page.get_by_role("dialog").first
    if dialog.count() == 0:
        return False

    cancel_btn = dialog.get_by_role("button", name="Cancel", exact=True)
    if cancel_btn.count() > 0:
        cancel_btn.first.click(timeout=5000)
        return True

    close_btn = page.locator(
        "[role='dialog'] button[aria-label*='close' i], "
        "[role='dialog'] button[title*='close' i], "
        "[role='dialog'] button[class*='close' i]"
    )
    if close_btn.count() > 0:
        close_btn.first.click(timeout=5000)
        return True
    return False


def process_invite_modal(page, send_request_label: str, timeout_ms: int) -> str:
    button = page.get_by_role("button", name=send_request_label, exact=True).first
    try:
        button.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return "no_modal"
    except PlaywrightError:
        return "no_modal"

    if has_modal_error(page):
        close_modal_if_open(page)
        return "blocked"

    try:
        button.click(timeout=10000)
    except PlaywrightTimeoutError:
        if has_modal_error(page):
            close_modal_if_open(page)
            return "blocked"
        return "send_timeout"
    except PlaywrightError:
        if has_modal_error(page):
            close_modal_if_open(page)
            return "blocked"
        return "send_error"

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        if has_modal_error(page):
            close_modal_if_open(page)
            return "blocked"
        if not is_dialog_open(page):
            return "sent"
        page.wait_for_timeout(300)

    if has_modal_error(page):
        close_modal_if_open(page)
        return "blocked"
    if close_modal_if_open(page):
        return "stalled_modal"
    return "stalled_modal"


def resolve_any_open_modal(page, send_request_label: str, timeout_ms: int) -> str:
    if not is_dialog_open(page):
        return "clear"
    status = process_invite_modal(page, send_request_label, timeout_ms)
    if status == "no_modal":
        if close_modal_if_open(page):
            return "closed_without_send"
        return "still_open"
    return status


def wait_for_connect_change(page, button_label: str, previous_count: int, timeout_ms: int) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        current = page.get_by_role("button", name=button_label, exact=True).count()
        if current < previous_count:
            return True
        page.wait_for_timeout(400)
    return False


def discover_connect_buttons(page, button_label: str, settle_timeout_ms: int) -> int:
    deadline = time.time() + (settle_timeout_ms / 1000.0)
    max_count = 0
    scroll_step = 900

    while time.time() < deadline:
        try:
            count = page.get_by_role("button", name=button_label, exact=True).count()
        except PlaywrightError:
            count = 0

        if count > max_count:
            max_count = count
        if max_count > 0:
            return max_count

        try:
            page.mouse.wheel(0, scroll_step)
            page.wait_for_timeout(250)
            page.mouse.wheel(0, -scroll_step)
        except PlaywrightError:
            pass
        page.wait_for_timeout(400)

    return max_count


def get_page_number_from_url(url: str) -> int | None:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    current_raw = query.get("page", [None])[0]
    if current_raw is None:
        return None
    try:
        return int(current_raw)
    except ValueError:
        return None


def with_page_number(url: str, page_number: int) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page_number)]
    next_query = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, next_query, parts.fragment))


def goto_with_retries(page, url: str, timeout_ms: int, retries: int) -> bool:
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            if attempt < retries:
                page.wait_for_timeout(800 * attempt)
                continue
            return False
        except PlaywrightError:
            if attempt < retries:
                page.wait_for_timeout(800 * attempt)
                continue
            return False
    return False


def go_to_next_page(
    page,
    timeout_ms: int,
    expected_next_page: int | None = None,
    max_attempts: int = 3,
    navigation_retries: int = 3,
) -> tuple[bool, int | None]:
    old_url = page.url
    old_page = get_page_number_from_url(old_url)
    if expected_next_page is None and old_page is not None:
        expected_next_page = old_page + 1

    if expected_next_page is not None:
        landed_page = None
        for _ in range(max_attempts):
            next_url = with_page_number(old_url, expected_next_page)
            ok = goto_with_retries(page, next_url, timeout_ms=timeout_ms, retries=navigation_retries)
            if not ok:
                continue
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
            landed_page = get_page_number_from_url(page.url)
            if landed_page == expected_next_page:
                return True, landed_page
        return False, landed_page

    next_arrow = page.locator(
        "a[aria-label*='next' i],button[aria-label*='next' i],a[rel='next']"
    ).first
    if next_arrow.count() == 0:
        return False, get_page_number_from_url(page.url)
    try:
        next_arrow.click(timeout=10000)
    except PlaywrightError:
        return False, get_page_number_from_url(page.url)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    new_url = page.url
    moved = new_url != old_url
    return moved, get_page_number_from_url(new_url)


def main():
    args = parse_args()
    if args.delay_seconds < 0:
        print("--delay-seconds must be >= 0")
        sys.exit(2)
    if args.max_clicks < 0:
        print("--max-clicks must be >= 0")
        sys.exit(2)
    if args.max_pages < 0:
        print("--max-pages must be >= 0")
        sys.exit(2)
    if args.modal_timeout_seconds < 0:
        print("--modal-timeout-seconds must be >= 0")
        sys.exit(2)
    if args.navigation_timeout_seconds <= 0:
        print("--navigation-timeout-seconds must be > 0")
        sys.exit(2)
    if args.navigation_retries <= 0:
        print("--navigation-retries must be > 0")
        sys.exit(2)
    if args.page_settle_seconds < 0:
        print("--page-settle-seconds must be >= 0")
        sys.exit(2)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp_url)
        except PlaywrightError as exc:
            print(f"Failed to connect to Chrome CDP at {args.cdp_url}: {exc}")
            print("Start Chrome with remote debugging enabled, then retry.")
            sys.exit(1)

        page = pick_page(browser, args.url_contains)
        if not page:
            print("No open tabs found in the connected Chrome instance.")
            sys.exit(1)

        print(f"Using tab: {page.url or '(blank)'}")
        print(
            f"Clicking buttons named exactly '{args.button_label}' "
            f"with {args.delay_seconds:.1f}s delay."
        )
        if args.max_clicks == 0 and args.max_pages == 0:
            print("No max-clicks/max-pages limit is set (unlimited run).")

        total_clicked = 0
        pages_processed = 0
        stop = False
        id_seq = 1
        skipped_button_ids = set()
        total_skipped = 0
        last_completed_page = None
        while True:
            if page.is_closed():
                print("Active page was closed.")
                break

            current_page_num = get_page_number_from_url(page.url)
            if (
                last_completed_page is not None
                and current_page_num is not None
                and current_page_num < last_completed_page
            ):
                recovery_page = last_completed_page + 1
                recovery_url = with_page_number(page.url, recovery_page)
                print(
                    "Detected page rollback "
                    f"(current={current_page_num}, expected>={recovery_page}). "
                    f"Trying recovery URL page={recovery_page}."
                )
                recovered = goto_with_retries(
                    page,
                    recovery_url,
                    timeout_ms=int(args.navigation_timeout_seconds * 1000),
                    retries=args.navigation_retries,
                )
                if not recovered:
                    print(
                        "Rollback recovery navigation timed out. "
                        f"URL attempted: {recovery_url}. Stopping."
                    )
                    break
                current_page_num = get_page_number_from_url(page.url)
                if current_page_num is None or current_page_num < last_completed_page:
                    print(
                        "Rollback recovery failed. "
                        f"Current URL: {page.url}. Stopping to avoid bad pagination."
                    )
                    break

            pages_processed += 1
            page_clicks = 0
            skipped_this_page = 0
            skipped_button_ids.clear()
            page_label = current_page_num if current_page_num is not None else "unknown"
            print(
                f"\nProcessing loop #{pages_processed}, URL page={page_label}: "
                f"{page.url or '(blank)'}"
            )

            while True:
                lingering_status = resolve_any_open_modal(
                    page,
                    args.send_request_label,
                    timeout_ms=int(args.modal_timeout_seconds * 1000),
                )
                if lingering_status == "still_open":
                    print("A modal is still open and could not be closed. Stopping.")
                    stop = True
                    break
                if lingering_status != "clear":
                    if lingering_status in {"blocked", "send_timeout", "send_error", "stalled_modal"}:
                        total_skipped += 1
                        skipped_this_page += 1
                        print(f"Resolved previous modal with status '{lingering_status}'. Continuing.")
                    elif lingering_status == "closed_without_send":
                        print("Closed lingering modal without sending. Continuing.")
                    if args.delay_seconds > 0:
                        time.sleep(args.delay_seconds)
                    continue

                buttons = page.get_by_role("button", name=args.button_label, exact=True)
                count = buttons.count()
                if count == 0:
                    discovered = discover_connect_buttons(
                        page,
                        args.button_label,
                        settle_timeout_ms=int(args.page_settle_seconds * 1000),
                    )
                    if discovered == 0:
                        print("No more matching buttons on this page (after settle check).")
                        break
                    buttons = page.get_by_role("button", name=args.button_label, exact=True)
                    count = buttons.count()

                selected = None
                selected_id = None
                for idx in range(count):
                    candidate = buttons.nth(idx)
                    button_id, id_seq = get_or_assign_button_id(candidate, id_seq)
                    if button_id in skipped_button_ids:
                        continue
                    selected = candidate
                    selected_id = button_id
                    break

                if selected is None:
                    print("All remaining Connect buttons on this page are skipped due to prior errors.")
                    break

                selected.scroll_into_view_if_needed(timeout=5000)
                try:
                    selected.click(timeout=10000)
                except PlaywrightTimeoutError as exc:
                    msg = str(exc)
                    if "intercepts pointer events" in msg or is_dialog_open(page):
                        if close_modal_if_open(page):
                            print("Click blocked by modal. Closed modal and continuing.")
                            if args.delay_seconds > 0:
                                time.sleep(args.delay_seconds)
                            continue
                    skipped_button_ids.add(selected_id)
                    skipped_this_page += 1
                    total_skipped += 1
                    print("Click timed out for this profile. Skipping and continuing.")
                    if args.delay_seconds > 0:
                        time.sleep(args.delay_seconds)
                    continue
                except PlaywrightError:
                    if close_modal_if_open(page):
                        print("Click failed due to modal state. Closed modal and continuing.")
                        if args.delay_seconds > 0:
                            time.sleep(args.delay_seconds)
                        continue
                    skipped_button_ids.add(selected_id)
                    skipped_this_page += 1
                    total_skipped += 1
                    print("Click failed for this profile. Skipping and continuing.")
                    if args.delay_seconds > 0:
                        time.sleep(args.delay_seconds)
                    continue

                modal_status = process_invite_modal(
                    page,
                    args.send_request_label,
                    timeout_ms=int(args.modal_timeout_seconds * 1000),
                )
                if modal_status in {"blocked", "send_timeout", "send_error", "stalled_modal"}:
                    skipped_button_ids.add(selected_id)
                    skipped_this_page += 1
                    total_skipped += 1
                    if modal_status == "blocked":
                        print("Profile returned modal error. Skipping and continuing.")
                    else:
                        print(f"Modal action failed ({modal_status}). Skipping profile and continuing.")
                    if args.delay_seconds > 0:
                        time.sleep(args.delay_seconds)
                    continue
                if modal_status == "sent":
                    print("Clicked modal submit: Send Request.")

                wait_for_connect_change(
                    page,
                    args.button_label,
                    previous_count=count,
                    timeout_ms=6000,
                )

                total_clicked += 1
                page_clicks += 1
                print(
                    f"Clicked {total_clicked} total "
                    f"(page clicks: {page_clicks}, remaining before click: {count})."
                )

                if args.max_clicks and total_clicked >= args.max_clicks:
                    print(f"Reached max clicks: {args.max_clicks}.")
                    stop = True
                    break

                if args.delay_seconds > 0:
                    time.sleep(args.delay_seconds)

            if stop:
                break

            if current_page_num is not None:
                last_completed_page = current_page_num

            if args.max_pages and pages_processed >= args.max_pages:
                print(f"Reached max pages: {args.max_pages}.")
                break

            if args.no_auto_next_page:
                print("Auto next page is disabled. Stopping.")
                break

            expected_next_page = current_page_num + 1 if current_page_num is not None else None
            moved, landed_page = go_to_next_page(
                page,
                timeout_ms=int(args.navigation_timeout_seconds * 1000),
                expected_next_page=expected_next_page,
                navigation_retries=args.navigation_retries,
            )
            if not moved:
                print(
                    "Could not confirm forward move to next page. "
                    f"Expected page={expected_next_page}, landed page={landed_page}, "
                    f"current URL={page.url}. Stopping."
                )
                break
            print(
                f"Moved to next page: {page.url} "
                f"(page={landed_page})"
            )

            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

        print(
            f"Done. Total clicks: {total_clicked}, "
            f"pages processed: {pages_processed}, skipped this run: {total_skipped}"
        )


if __name__ == "__main__":
    main()

