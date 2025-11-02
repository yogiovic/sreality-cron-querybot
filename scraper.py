#!/usr/bin/env python3

# Rewritten scraper: robust JSON extraction from <script> tags, pagination follow, dedupe,
# save per-page and aggregated results (only relevant JSON files kept), and print clickable URLs.

import argparse
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys
from urllib.parse import urljoin, urlparse
import unicodedata

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'cs,en;q=0.9'
}

OUT_DIR = os.path.abspath(os.path.dirname(__file__))
BASE_DOMAIN = 'https://www.sreality.cz'


def save_file(name, content):
    path = os.path.join(OUT_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        if isinstance(content, (dict, list)):
            json.dump(content, f, ensure_ascii=False, indent=2)
        else:
            f.write(content)
    return path


def fetch_page(url, timeout=20):
    print(f"Fetching: {url}")
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    print(f"HTTP {resp.status_code}, content-length {len(resp.content)} bytes")
    return resp.text


def try_extract_json_from_text(text):
    """Return list of json-parsed objects found in the given text by heuristics."""
    objs = []
    if not text:
        return objs
    t = text.strip()
    # Direct parse
    try:
        if t.startswith('{') or t.startswith('['):
            objs.append(json.loads(t))
            return objs
    except Exception:
        pass

    # Assignment like: window.__INITIAL_STATE__ = {...};
    m = re.search(r'=[\s\n]*(\{.*\})[\s\n]*;?$', t, re.S)
    if m:
        try:
            objs.append(json.loads(m.group(1)))
            return objs
        except Exception:
            pass

    # Try to extract large {...} blocks using brace counting
    start = t.find('{')
    while start != -1:
        depth = 0
        for j in range(start, len(t)):
            if t[j] == '{':
                depth += 1
            elif t[j] == '}':
                depth -= 1
                if depth == 0:
                    candidate = t[start:j+1]
                    try:
                        objs.append(json.loads(candidate))
                    except Exception:
                        pass
                    start = t.find('{', j+1)
                    break
        else:
            break
    return objs


def find_results_lists(obj):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'results' and isinstance(v, list) and v and isinstance(v[0], dict):
                found.append(v)
            else:
                found.extend(find_results_lists(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_results_lists(item))
    return found


def find_json_candidates(soup):
    scripts = soup.find_all('script')
    candidates = []
    for i, s in enumerate(scripts):
        content = None
        if s.string:
            content = s.string
        else:
            content = s.get_text(separator=' ', strip=True)
        if not content:
            continue
        # Heuristic: long script content and JSON-ish patterns
        if len(content) > 200 and re.search(r'\b(results|estates|offers|offersList|seoUrl|__INITIAL_STATE__|props)\b', content, re.I):
            candidates.append((i, content))
    return candidates


def extract_results_from_html(html, page_num=1, save_candidates=False, save_items=False):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = find_json_candidates(soup)
    results_lists = []

    for idx, (i, content) in enumerate(candidates):
        objs = try_extract_json_from_text(content)
        for obj in objs:
            results_lists.extend(find_results_lists(obj))
        # save candidate only when requested
        if save_candidates:
            save_file(f'script_candidate_p{page_num}_{i}.txt', content)

    print(f"Page {page_num}: Found {len(candidates)} JSON-like <script> candidate(s), extracted {len(results_lists)} results-list(s)")

    if not results_lists:
        return [], soup

    # Choose the longest results list (most likely the main one)
    results = max(results_lists, key=lambda x: len(x))

    # Save page-level results
    save_file(f'results_p{page_num}.json', results)

    # Save individual items only when requested
    if save_items:
        for i, r in enumerate(results, 1):
            save_file(f'extracted_p{page_num}_{i}.json', r)

    return results, soup


def find_next_page_url(soup, current_url):
    # 1) <link rel="next">
    link = soup.find('link', rel=lambda x: x and 'next' in x.lower())
    if link and link.get('href'):
        return urljoin(current_url, link.get('href'))

    # 2) anchor with rel="next"
    a = soup.find('a', rel=lambda x: x and 'next' in x.lower())
    if a and a.get('href'):
        return urljoin(current_url, a.get('href'))

    # 3) anchor with aria-label/title containing 'další' or 'next'
    for a in soup.find_all('a', href=True):
        txt = (a.get('aria-label') or a.get('title') or a.get_text()).strip()
        if not txt:
            continue
        if re.search(r'(^|\s)(next|další|další »|›|»)(\s|$)', txt, re.I):
            return urljoin(current_url, a.get('href'))

    # 4) anchors with class or href pattern containing 'strana' or 'page'
    for a in soup.find_all('a', href=True):
        cls = ' '.join(a.get('class') or [])
        href = a.get('href')
        if 'next' in cls.lower() or 'paging' in cls.lower() or 'pager' in cls.lower() or ('strana' in href.lower() or 'page=' in href.lower()):
            # ensure it's a different page link
            if urljoin(current_url, href) != current_url:
                return urljoin(current_url, href)

    return None


def get_listing_url(item):
    if not isinstance(item, dict):
        return None
    # Common fields that contain URL or SEO path
    for key in ('seoUrl', 'seo_url', 'seoUri', 'url', 'canonical', 'permalink', 'href'):
        v = item.get(key)
        if v:
            if isinstance(v, (int, float)):
                v = str(v)
            if v.startswith('http'):
                return v
            # sometimes it's like '/detail/..'
            return urljoin(BASE_DOMAIN, v)

    # sometimes nested under 'seo' or 'link'
    for key in ('seo', 'link'):
        v = item.get(key)
        if isinstance(v, dict):
            for kk in ('url', 'href', 'seoUrl'):
                vv = v.get(kk)
                if vv:
                    return urljoin(BASE_DOMAIN, vv) if not vv.startswith('http') else vv
    # fallback: if we have 'hash' or 'id' we can build a detail URL
    idv = item.get('id') or item.get('hash')
    # NEW: try to build SEO-friendly URL using category and locality fields
    def norm(s):
        if not s:
            return ''
        if not isinstance(s, str):
            s = str(s)
        s = s.lower()
        s = unicodedata.normalize('NFKD', s)
        s = ''.join(ch for ch in s if not unicodedata.combining(ch))
        s = s.replace('&', 'a')
        s = re.sub(r"[^a-z0-9\-\s]", '', s)
        s = re.sub(r"[\s]+", '-', s)
        s = s.strip('-')
        return s

    # Build seo path pieces
    if isinstance(item, dict):
        # transaction type: e.g., Prodej -> prodej
        trans = None
        if item.get('categoryTypeCb') and isinstance(item['categoryTypeCb'], dict):
            trans = norm(item['categoryTypeCb'].get('name'))
        # main type and sub type
        main = None
        sub = None
        if item.get('categoryMainCb') and isinstance(item['categoryMainCb'], dict):
            main = norm(item['categoryMainCb'].get('name'))
        if item.get('categorySubCb') and isinstance(item['categorySubCb'], dict):
            sub = norm(item['categorySubCb'].get('name'))

        # prefer short canonical main type mapping (plural->singular) for common categories
        main_map = {
            'domy': 'dum', 'byty': 'byt', 'pozemky': 'pozemek', 'garaze': 'garaz', 'prodej': 'prodej'
        }
        main_slug = main_map.get(main, main) if main else None

        # locality pieces
        locality = item.get('locality') or {}
        city = norm(locality.get('citySeoName') or locality.get('city') or '')
        citypart = norm(locality.get('cityPartSeoName') or locality.get('cityPart') or '')
        street = norm(locality.get('streetSeoName') or locality.get('street') or '')

        location_parts = [p for p in (city, citypart, street) if p]
        location_slug = '-'.join(location_parts) if location_parts else ''

        if idv:
            pieces = ['detail']
            if trans:
                pieces.append(trans)
            if main_slug:
                pieces.append(main_slug)
            elif main:
                pieces.append(main)
            if sub:
                pieces.append(sub)
            if location_slug:
                pieces.append(location_slug)
            pieces.append(str(idv))
            path = '/' + '/'.join(pieces)
            return urljoin(BASE_DOMAIN, path)

    # last fallback
    if idv:
        return urljoin(BASE_DOMAIN, f"/detail/{idv}")
    return None


def scrape_all_pages(start_url, max_pages=50, keep_individual=False):
    current = start_url
    page_num = 1
    all_results = []
    seen_ids = set()

    while current and page_num <= max_pages:
        html = fetch_page(current)
        save_file(f'page_raw_p{page_num}.html', html)
        results, soup = extract_results_from_html(html, page_num=page_num, save_candidates=keep_individual, save_items=keep_individual)

        for r in results:
            rid = None
            if isinstance(r, dict):
                rid = r.get('id') or r.get('hash') or r.get('seoUrl')
            # dedupe if id present
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            all_results.append(r)

        next_url = find_next_page_url(soup, current)
        if not next_url:
            print(f"No next page found after page {page_num}.")
            break
        if next_url == current:
            print("Next page URL same as current, stopping to avoid loop.")
            break

        print(f"Page {page_num} -> next: {next_url}")
        current = next_url
        page_num += 1

    # Save aggregated results
    # Inject a canonical listing URL into each item so results.json contains clickable SEO-friendly links
    for r in all_results:
        try:
            url = get_listing_url(r)
        except Exception:
            url = None
        if url:
            # do not override if user already provided a canonical url field; store under 'listingUrl'
            r['listingUrl'] = url

    save_file('results.json', all_results)
    print(f"Aggregated total results: {len(all_results)} (saved to results.json)")

    # Optionally save per-item files (kept only when keep_individual True)
    if keep_individual:
        for i, r in enumerate(all_results, 1):
            save_file(f'result_{i}.json', r)

    # Print all results with clickable URLs
    print('\nAll found results:')
    for i, r in enumerate(all_results, 1):
        name = r.get('name') or r.get('title') or r.get('headline') if isinstance(r, dict) else ''
        rid = r.get('id') if isinstance(r, dict) else None
        price = ''
        if isinstance(r, dict):
            price = r.get('priceSummaryCzk') or r.get('priceCzk') or r.get('price') or ''
        url = get_listing_url(r) or ''
        # Print index, name, id, price, and full URL (clickable in most terminals/IDE consoles)
        print(f"{i}. {name} | id={rid} | price={price} | {url}")

    return all_results


def cleanup_old_artifacts(keep_files=None, keep_patterns=None):
    """Remove old files that match common artifact patterns, but keep files listed in keep_files or matching keep_patterns."""
    if keep_files is None:
        keep_files = set()
    if keep_patterns is None:
        keep_patterns = set()
    # Patterns to remove by default
    patterns = [
        'result_*.json', 'result_p*_*', 'results_p*.json', 'results_merged.json',
        'extracted_*.json', 'extracted_p*_*', 'script_candidate_*.txt',
        'page_raw.html', 'page_pretty.html', 'page_raw_p*.html', 'page_pretty_p*.html'
    ]
    removed = []
    from fnmatch import fnmatch
    for root, dirs, files in os.walk(OUT_DIR):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, OUT_DIR)
            if rel in keep_files or fname in keep_files:
                continue
            skip = False
            for kp in keep_patterns:
                if fnmatch(fname, kp):
                    skip = True
                    break
            if skip:
                continue
            for pat in patterns:
                if fnmatch(fname, pat):
                    try:
                        os.remove(fpath)
                        removed.append(rel)
                    except Exception:
                        pass
                    break
    print(f"Cleaned {len(removed)} old artifact(s): {removed}")
    return removed


def main():
    parser = argparse.ArgumentParser(description='Scrape Sreality search page and extract embedded JSON results')
    parser.add_argument('--url', '-u', help='Start URL to scrape', required=False)
    parser.add_argument('--max-pages', type=int, default=50, help='Maximum pages to follow')
    parser.add_argument('--keep-items', action='store_true', help='Keep per-item json files')
    parser.add_argument('--clean', action='store_true', help='Remove old artifact files before running')
    args = parser.parse_args()

    start_url = args.url or os.environ.get('SCRAPE_URL') or ''
    if not start_url:
        print('No URL provided. Use --url or set SCRAPE_URL env var.')
        sys.exit(1)

    if args.clean:
        # keep results.json and per-run results_p*.json
        keep = {'results.json'}
        keep_pats = {'results_p*.json'}
        cleanup_old_artifacts(keep_files=keep, keep_patterns=keep_pats)

    all_results = scrape_all_pages(start_url, max_pages=args.max_pages, keep_individual=args.keep_items)


if __name__ == '__main__':
    main()
