#!/usr/bin/env python3
"""
extractHunterEmails.py

Queries the Hunter.io domain-search endpoint for a given domain, paginating
through all results (Hunter caps `limit` at 100 per request), saves the raw
JSON responses to <domain>.raw, and extracts all email addresses found into
<domain>.emails (one per line).

Usage:
    python3 extractHunterEmails.py <domain> <api_key> [--limit LIMIT]

Example:
    python3 extractHunterEmails.py example.com YOUR_API_KEY
    python3 extractHunterEmails.py example.com YOUR_API_KEY --limit 50
"""

import argparse
import json
import sys
import time
from urllib.parse import urlencode

import urllib.request
import urllib.error

HUNTER_URL = "https://api.hunter.io/v2/domain-search"
MAX_LIMIT = 100  # Hunter's per-request cap for domain-search


def fetch_page(domain: str, api_key: str, limit: int, offset: int) -> bytes:
    """Perform a single HTTP GET request against the Hunter.io API and return raw bytes."""
    params = {
        "domain": domain,
        "api_key": api_key,
        "limit": limit,
        "offset": offset,
    }
    url = f"{HUNTER_URL}?{urlencode(params)}"

    req = urllib.request.Request(url, headers={"User-Agent": "hunter-domain-search-script/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        # Still return the body, since Hunter often includes useful error JSON
        body = e.read()
        print(f"[!] HTTP error {e.code}: {e.reason} (offset={offset})", file=sys.stderr)
        try:
            err_json = json.loads(body.decode("utf-8"))
            print(f"[!] Details: {json.dumps(err_json)}", file=sys.stderr)
        except Exception:
            pass
        return body
    except urllib.error.URLError as e:
        print(f"[!] Request failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


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


def main():
    parser = argparse.ArgumentParser(description="Query Hunter.io domain-search API and extract all emails, paginating as needed.")
    parser.add_argument("domain", help="Target domain, e.g. example.com")
    parser.add_argument("api_key", help="Hunter.io API key")
    parser.add_argument("--limit", type=int, default=MAX_LIMIT,
                         help=f"Per-request page size, max {MAX_LIMIT} (default: {MAX_LIMIT})")
    parser.add_argument("--delay", type=float, default=0.5,
                         help="Delay in seconds between paginated requests (default: 0.5)")
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
    page_num = 1

    while True:
        print(f"[*] Fetching page {page_num} for {domain} (offset={offset}, limit={limit})")
        raw_bytes = fetch_page(domain, args.api_key, limit, offset)

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

        # Stop conditions:
        # 1. No emails returned on this page -> nothing more to fetch
        # 2. We've reached/exceeded the total_results count reported by the API
        if len(emails) == 0:
            break
        if total_results is not None and offset + limit >= total_results:
            break

        offset += limit
        page_num += 1
        time.sleep(args.delay)

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
