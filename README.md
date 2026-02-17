# PMI Community Connections Safe Click Helper

This tool assists with clicking PMI Community Connections buttons in a controlled, manual-confirmation workflow.

## Safety behavior

- Uses headed browser mode (`headless=False`).
- Uses a persistent Playwright profile (`user_data_dir`) so your existing login session can be reused.
- Requires explicit confirmation before each click.
- Requires explicit confirmation before moving to the next page.
- Re-queries the page before each click, and clicks exactly one button each time.
- Detects likely CAPTCHA/verification prompts and pauses for manual solve.
- Saves screenshot after each click to `./screenshots/`.
- Writes logs to `./logs/run.log`.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install
python click_connect_helper.py --start-url "https://community.pmi.org/profile/<username>/tab=connections?section=connections&page=1" --button-label "Connect"
```

Use an already-open tab instead of forcing a start URL:

```bash
python click_connect_helper.py --use-open-page --button-label "Connect"
```

Use system Chrome (recommended if site behaves differently in Playwright Chromium):

```bash
python click_connect_helper.py --use-open-page --button-label "Connect" --browser-channel chrome --user-data-dir "./user_data"
```

If Chrome startup crashes, use a local (non-OneDrive) profile path:

```bash
python click_connect_helper.py --use-open-page --button-label "Connect" --browser-channel chromium --user-data-dir "$env:LOCALAPPDATA\\pmi_playwright_profile"
```

## Arguments

- `--start-url` (required unless `--use-open-page`): First page URL to open.
- `--button-label` (default: `Connect`): Exact accessible button label to target.
- `--timeout-seconds` (default: `15`): Max wait for post-click UI change.
- `--user-data-dir` (default: `./user_data`): Persistent browser profile path.
- `--screenshot-dir` (default: `./screenshots`): Screenshot output path.
- `--log-file` (default: `./logs/run.log`): Log file path.
- `--min-delay-seconds` (default: `2.0`): Minimum post-click delay.
- `--max-delay-seconds` (default: `4.0`): Maximum post-click delay.
- `--use-open-page`: Select and use an already-open tab in the persistent profile.
- `--browser-channel` (default: `chromium`): Browser channel (`chromium`, `chrome`, or `msedge`).

## Example

```bash
python click_connect_helper.py \
  --start-url "https://community.pmi.org/profile/<username>/tab=connections?section=connections&page=1" \
  --button-label "Connect" \
  --user-data-dir "./user_data"
```

## Notes

- Do not include credentials in code; log in manually using the opened browser window.
- No stealth plugins, fingerprint spoofing, bot-detection evasion, or full auto-run loops are used.
- If you see PMI's "Something unexpected happened" page, recover manually in the browser and press Enter to continue.

## Simple Attach Mode (Open page first in Chrome)

1. Start Chrome with remote debugging enabled:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

2. In that Chrome window, open your PMI connections page manually.

3. Run:

```powershell
python simple_click_connect_attach.py --button-label "Connect" --url-contains "community.pmi.org"
```

This mode now:
- Clicks `Connect`
- If invite modal appears, clicks `Send Request`
- If modal shows an error (for example, `Cannot read properties of undefined...`), it cancels and skips that profile
- Continues until no Connect buttons remain on current page
- Moves automatically using URL `page=` increments and verifies forward page movement

Useful options:

```powershell
python simple_click_connect_attach.py --button-label "Connect" --url-contains "community.pmi.org" --max-pages 5 --delay-seconds 2
```

By default there is no page/click cap (`--max-pages 0` and `--max-clicks 0`).
If pages load slowly, increase settle time so pages are not treated as empty too early:

```powershell
python simple_click_connect_attach.py --button-label "Connect" --url-contains "community.pmi.org" --page-settle-seconds 10
```

If PMI navigation is unstable/slow, increase timeout and retries:

```powershell
python simple_click_connect_attach.py --button-label "Connect" --url-contains "community.pmi.org" --navigation-timeout-seconds 30 --navigation-retries 5
```
