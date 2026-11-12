#!/usr/bin/env python3
"""
extractHunterEmails.py

Queries the Hunter.io domain-search endpoint for a given domain, paginating
through all results (Hunter caps `limit` at 100 per request), saves the raw
JSON responses to <domain>.raw, and extracts all email addresses found into
<domain>.emails (one per line).

Before pulling the full result set, the script does a cheap 1-result probe
to find out how many emails Hunter has for the domain, checks your account's
remaining search credits, and asks you to confirm before spending them.
The credit check and estimate are always shown (even with --force), which
only skips the confirmation prompt itself.

Usage:
    python3 extractHunterEmails.py <domain> <api_key> [--limit LIMIT] [--force]

Example:
    python3 extractHunterEmails.py example.com YOUR_API_KEY
    python3 extractHunterEmails.py example.com YOUR_API_KEY --limit 50
    python3 extractHunterEmails.py example.com YOUR_API_KEY --force
"""

import argparse
import json
import sys
import time
from urllib.parse import urlencode

import urllib.request
import urllib.error

HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_ACCOUNT_URL = "https://api.hunter.io/v2/account"
MAX_LIMIT = 100  # Hunter's per-request cap for domain-search
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"


def http_get(url: str) -> bytes:
    """Perform a GET request and return the raw response bytes (even on HTTP errors)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"[!] HTTP error {e.code}: {e.reason} ({url.split('?')[0]})", file=sys.stderr)
        try:
            err_json = json.loads(body.decode("utf-8"))
            print(f"[!] Details: {json.dumps(err_json)}", file=sys.stderr)
        except Exception:
            pass
        return body
    except urllib.error.URLError as e:
        print(f"[!] Request failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


def fetch_page(domain: str, api_key: str, limit: int, offset: int) -> bytes:
    """Perform a single domain-search request and return raw bytes."""
    params = {
        "domain": domain,
        "api_key": api_key,
        "limit": limit,
        "offset": offset,
    }
    url = f"{HUNTER_DOMAIN_SEARCH_URL}?{urlencode(params)}"
    return http_get(url)


def parse_page(raw_bytes: bytes):
    """Parse a page of JSON. Returns (email_list, total_results, error_occurred)."""
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[!] Failed to parse JSON response: {e}", file=sys.stderr)
        return [], None, True

    if "errors" in data:
        return [], None, True

    email_objs = data.get("data", {}).get("emails", [])
    emails = [obj.get("value") for obj in email_objs if obj.get("value")]

    total_results = data.get("meta", {}).get("results")

    return emails, total_results, False


def get_account_credits(api_key: str):
    """
    Query /v2/account for the remaining and used search credits.
    Returns (available, used) as ints, or (None, None) if unavailable.
    """
    url = f"{HUNTER_ACCOUNT_URL}?{urlencode({'api_key': api_key})}"
    raw_bytes = http_get(url)
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None

    if "errors" in data:
        return None, None

    searches = data.get("data", {}).get("requests", {}).get("searches", {})
    available = searches.get("available")
    used = searches.get("used")
    return available, used


def probe_total_results(domain: str, api_key: str):
    """
    Do a minimal (limit=1) domain-search request purely to learn how many
    total email results Hunter reports for this domain, without pulling
    the full set yet. Returns (total_results, first_page_raw_bytes).
    """
    raw_bytes = fetch_page(domain, api_key, limit=1, offset=0)
    _, total_results, error_occurred = parse_page(raw_bytes)
    if error_occurred:
        return None, raw_bytes
    return total_results, raw_bytes


def confirm(prompt_text: str) -> bool:
    """Ask the user a yes/no question on the terminal."""
    try:
        answer = input(f"{prompt_text} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(description="Query Hunter.io domain-search API and extract all emails, paginating as needed.")
    parser.add_argument("domain", help="Target domain, e.g. example.com")
    parser.add_argument("api_key", help="Hunter.io API key")
    parser.add_argument("--limit", type=int, default=MAX_LIMIT,
                         help=f"Per-request page size, max {MAX_LIMIT} (default: {MAX_LIMIT})")
    parser.add_argument("--delay", type=float, default=0.5,
                         help="Delay in seconds between paginated requests (default: 0.5)")
    parser.add_argument("--force", "-f", action="store_true",
                         help="Skip the confirmation prompt and proceed automatically "
                              "(the credit check/estimate is still shown)")
    args = parser.parse_args()

    domain = args.domain
    limit = min(args.limit, MAX_LIMIT)
    raw_filename = f"{domain}.raw"
    emails_filename = f"{domain}.emails"

    all_pages = []       # list of parsed JSON page objects (for the raw file)
    all_emails = []      # deduplicated, ordered list of email addresses
    seen_emails = set()

    offset = 0
    total_results = None
    first_page_raw = None

    # Always check credits/estimated cost, even with --force, so a negative
    # remaining-credit warning is never silently skipped. --force only skips
    # the confirmation prompt afterward.
    print(f"[*] Checking account credits and estimated cost for '{domain}'...")

    available, used = get_account_credits(args.api_key)
    total_results, first_page_raw = probe_total_results(domain, args.api_key)

    if total_results is None:
        print("[!] Could not determine result count in advance (probe request failed or errored).", file=sys.stderr)
        print("[!] Proceeding without a credit estimate is risky; aborting.", file=sys.stderr)
        sys.exit(1)

    estimated_cost = total_results  # Hunter bills ~1 search credit per email result returned

    print(f"[*] Domain '{domain}' has approximately {total_results} email result(s) available.")
    if available is not None:
        print(f"[*] Account search credits: {available} available, {used if used is not None else '?'} used so far.")
        print(f"[*] Estimated cost of this query: ~{estimated_cost} credit(s).")
        remaining_after = available - estimated_cost
        print(f"[*] Estimated remaining after this query: ~{remaining_after} credit(s).")
        if remaining_after < 0:
            print("[!] WARNING: estimated cost exceeds your available credits.", file=sys.stderr)
    else:
        print("[!] Could not retrieve account credit info (continuing without it).", file=sys.stderr)

    if not args.force:
        proceed = confirm("Proceed with fetching all results?")
        if not proceed:
            print("[*] Aborted by user. No further requests were made.")
            sys.exit(0)
    else:
        print("[*] --force specified: skipping confirmation prompt, proceeding automatically.")

    # First page: reuse the probe's result if we have it (it already consumed
    # the limit=1 request), otherwise just fetch normally.
    page_num = 1
    if first_page_raw is not None and limit == 1:
        # Edge case: user explicitly requested limit=1; the probe page IS the real first page.
        raw_bytes = first_page_raw
    else:
        print(f"[*] Fetching page {page_num} for {domain} (offset={offset}, limit={limit})")
        raw_bytes = fetch_page(domain, args.api_key, limit, offset)

    while True:
        try:
            page_json = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            page_json = {"_raw_decode_error": True, "_raw_bytes_repr": repr(raw_bytes)}

        all_pages.append(page_json)

        emails, total_results_page, error_occurred = parse_page(raw_bytes)
        if error_occurred:
            print("[!] Stopping pagination due to error response.", file=sys.stderr)
            break

        for e in emails:
            if e not in seen_emails:
                seen_emails.add(e)
                all_emails.append(e)

        if total_results_page is not None:
            total_results = total_results_page

        print(f"    -> got {len(emails)} email(s) this page, {len(all_emails)} total so far"
              + (f" / {total_results} reported by API" if total_results is not None else ""))

        if len(emails) == 0:
            break
        if total_results is not None and offset + limit >= total_results:
            break

        offset += limit
        page_num += 1
        time.sleep(args.delay)

        print(f"[*] Fetching page {page_num} for {domain} (offset={offset}, limit={limit})")
        raw_bytes = fetch_page(domain, args.api_key, limit, offset)

    # Save raw response (all pages, as a JSON array)
    with open(raw_filename, "w") as f:
        json.dump(all_pages, f, indent=2)
    print(f"[+] Raw response(s) saved to {raw_filename} ({len(all_pages)} page(s))")

    # Save emails
    with open(emails_filename, "w") as f:
        for email in all_emails:
            f.write(email + "\n")

    print(f"[+] Extracted {len(all_emails)} unique email(s) to {emails_filename}")


if __name__ == "__main__":
    main()
