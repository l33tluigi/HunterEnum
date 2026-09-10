#!/usr/bin/env python3
"""
extractFormats.py

Scans raw files (produced by extractHunterEmails.py) in a directory and
extracts each domain's email naming pattern into a single summary file.

Handles both raw file formats:
  - A single JSON object: {"data": {"domain": ..., "pattern": ...}, ...}
  - A JSON array of pages (as produced by the paginating script):
    [{"data": {...}}, {"data": {...}}, ...]

Usage:
    python3 extract_patterns.py [directory] [--output patterns.txt]

Example:
    python3 extract_patterns.py .
    python3 extract_patterns.py /path/to/raw/files --output all_patterns.csv
"""

import argparse
import glob
import json
import os
import sys


def load_raw_file(path: str):
    """Load a .raw file and return it as a list of page dicts."""
    with open(path, "r", encoding="utf-8") as f:
        content = json.load(f)

    if isinstance(content, list):
        return content
    elif isinstance(content, dict):
        return [content]
    else:
        return []


def extract_domain_pattern(pages: list):
    """Pull domain + pattern from the first page that has them."""
    for page in pages:
        data = page.get("data") if isinstance(page, dict) else None
        if not data:
            continue
        domain = data.get("domain")
        pattern = data.get("pattern")
        organization = data.get("organization")
        if domain is not None:
            return domain, pattern, organization
    return None, None, None


def main():
    parser = argparse.ArgumentParser(description="Extract domain + email pattern from Hunter.io .raw files.")
    parser.add_argument("directory", nargs="?", default=".", help="Directory containing .raw files (default: current directory)")
    parser.add_argument("--output", default="patterns.txt", help="Output filename (default: patterns.txt)")
    parser.add_argument("--format", choices=["txt", "csv"], default="txt",
                         help="Output format: 'txt' (domain: pattern) or 'csv' (domain,pattern,organization)")
    args = parser.parse_args()

    raw_files = sorted(glob.glob(os.path.join(args.directory, "*.raw")))

    if not raw_files:
        print(f"[!] No .raw files found in {args.directory}", file=sys.stderr)
        sys.exit(1)

    results = []
    for path in raw_files:
        try:
            pages = load_raw_file(path)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[!] Skipping {path}: failed to parse JSON ({e})", file=sys.stderr)
            continue

        domain, pattern, organization = extract_domain_pattern(pages)

        if domain is None:
            # Fall back to filename (strip .raw) if domain missing from response
            domain = os.path.basename(path)[:-4]
            print(f"[!] {path}: 'domain' not found in response, using filename", file=sys.stderr)

        results.append((domain, pattern, organization))
        print(f"[+] {domain}: pattern={pattern!r} org={organization!r}")

    with open(args.output, "w", encoding="utf-8") as f:
        if args.format == "csv":
            f.write("domain,pattern,organization\n")
            for domain, pattern, organization in results:
                # basic CSV-safe quoting
                def q(v):
                    v = "" if v is None else str(v)
                    if "," in v or '"' in v:
                        v = '"' + v.replace('"', '""') + '"'
                    return v
                f.write(f"{q(domain)},{q(pattern)},{q(organization)}\n")
        else:
            for domain, pattern, organization in results:
                f.write(f"{domain}: {pattern}\n")

    print(f"\n[+] Wrote {len(results)} entries to {args.output}")


if __name__ == "__main__":
    main()
