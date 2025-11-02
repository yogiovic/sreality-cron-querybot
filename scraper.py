#!/usr/bin/env python3

import argparse
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys
from urllib.parse import urljoin

DEFAULT_URL = "https://www.sreality.cz/hledani/prodej/pozemky/komercni-pozemky,ostatni-pozemky,stavebni-parcely?plocha-od=2000&region=Hradec%20Kr%C3%A1lov%C3%A9&region-id=2149&region-typ=municipality&vzdalenost=25&q=bytovy%20dum"

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'cs,en;q=0.9'
}

out_dir = os.path.abspath(os.path.dirname(__file__))

# Helpers

def save_file(name, content):
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def fetch_page(url, timeout=20):
    print(f"Fetching: {url}")
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    print(f"HTTP {resp.status_code}, content-length {len(resp.content)} bytes")
    return resp.text


def find_json_candidates(soup):
    scripts = soup.find_all('script')
    candidates = []
    for i, s in enumerate(scripts):
        content = s.string if s.string is not None else s.get_text(separator=' ', strip=True)
        if not content:
            continue
        # Heuristic: long script content and JSON-ish patterns
        if len(content) > 200 and (re.search(r'\{\s*"props"|"results"|"estates"|"offers"|"offersList"|"seoUrl"', content, re.I) or content.strip().startswith('{') or content.strip().startswith('[')):
            candidates.append((i, content))
    return candidates


def try_extract_json_from_text(text):
    objs = []
    # Try direct JSON parse if it starts like JSON
    t = text.strip()
    if t.startswith('{') or t.startswith('['):
        try:
            objs.append(json.loads(t))
            return objs
        except Exception:
            pass
    # Try to find assignment patterns like window.__NEXT_DATA__ = {...};
    m = re.search(r'=[\s\n]*(\{.*\})[\s\n]*;?$', text.strip(), re.S)
    if m:
        try:
            objs.append(json.loads(m.group(1)))
            return objs
        except Exception:
            pass
    # Fallback: find large JSON-looking blocks using naive brace balancing
    start = text.find('{')
    while start != -1:
        depth = 0
        for j in range(start, len(text)):
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:j+1]
                    try:
                        objs.append(json.loads(candidate))
                    except Exception:
                        pass
                    # continue searching after this block
                    start = text.find('{', j+1)
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


# New: try to find "next page" link in the HTML
def find_next_page_url(soup, current_url):
    # 1) <link rel="next" href="...">
    link = soup.find('link', rel=lambda x: x and 'next' in x.lower())
    if link and link.get('href'):
        return urljoin(current_url, link.get('href'))

    # 2) anchor with rel="next"
    a = soup.find('a', rel=lambda x: x and 'next' in x.lower())
    if a and a.get('href'):
        return urljoin(current_url, a.get('href'))

    # 3) anchor with aria-label or title containing next / další
    for a in soup.find_all('a'):
        txt = (a.get('aria-label') or a.get('title') or a.get_text()).strip()
        if not txt:
            continue
        if re.search(r'(^|\s)(next|další|další »|›|»)(\s|$)', txt, re.I):
            href = a.get('href')
            if href:
                return urljoin(current_url, href)

    # 4) anchor with class name containing 'next' or 'paging'
    for a in soup.find_all('a', href=True):
        cls = ' '.join(a.get('class') or [])
        if 'next' in cls.lower() or 'paging' in cls.lower() or 'pager' in cls.lower() or 'strana' in a.get('href', '').lower():
            # ensure it's a different page link
            href = a.get('href')
            if href and urljoin(current_url, href) != current_url:
                return urljoin(current_url, href)

    return None


# New: extract results from a single page HTML, saving artifacts with page suffix
def extract_results_from_html(html, page_num=1):
    soup = BeautifulSoup(html, 'html.parser')
    page_pretty = save_file(f'page_pretty_p{page_num}.html', soup.prettify())

    # find JSON candidates
    candidates = find_json_candidates(soup)
    print(f"Page {page_num}: Found {len(candidates)} JSON-like <script> candidate(s)")
    for idx, (i, content) in enumerate(candidates[:20]):
        path = save_file(f'script_candidate_p{page_num}_{i}.txt', content)
        print(f"Saved script candidate #{idx+1} to: {path}")

    # extract JSON objects
    extracted_objs = []
    for i, content in candidates:
        objs = try_extract_json_from_text(content)
        if objs:
            extracted_objs.extend(objs)
    # save extracted objects
    for k, obj in enumerate(extracted_objs):
        path = save_file(f'extracted_p{page_num}_{k}.json', json.dumps(obj, ensure_ascii=False, indent=2))
        print(f"Saved extracted JSON #{k+1} to: {path}")

    # Try to find results lists inside extracted objects
    results_lists = []
    for obj in extracted_objs:
        results_lists.extend(find_results_lists(obj))

    # Fallback: search extracted files we saved earlier (per-page)
    if not results_lists:
        for fname in os.listdir(out_dir):
            if fname.startswith(f'extracted_p{page_num}_') and fname.endswith('.json'):
                try:
                    with open(os.path.join(out_dir, fname), 'r', encoding='utf-8') as f:
                        obj = json.load(f)
                    results_lists.extend(find_results_lists(obj))
                    if results_lists:
                        break
                except Exception:
                    continue

    if not results_lists:
        print(f"Page {page_num}: No 'results' arrays found in extracted JSON objects.")
        return [], soup

    # take the first results list
    results = results_lists[0]
    save_file(f'results_p{page_num}.json', json.dumps(results, ensure_ascii=False, indent=2))
    print(f"Page {page_num}: Found results list with {len(results)} items (saved to results_p{page_num}.json)")

    # Save individual items (per-page)
    for i, r in enumerate(results, 1):
        save_file(f'result_p{page_num}_{i}.json', json.dumps(r, ensure_ascii=False, indent=2))

    return results, soup


# New: crawl all pages starting from a URL, following next links and aggregating results
def scrape_all_pages(start_url, max_pages=50):
    current = start_url
    page_num = 1
    all_results = []
    seen_ids = set()

    while current and page_num <= max_pages:
        html = fetch_page(current)
        save_file(f'page_raw_p{page_num}.html', html)
        results, soup = extract_results_from_html(html, page_num=page_num)

        # aggregate and deduplicate by id when possible
        for r in results:
            rid = r.get('id') if isinstance(r, dict) else None
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            all_results.append(r)

        # find next page
        next_url = find_next_page_url(soup, current)
        if not next_url:
            print(f"No next page found after page {page_num}.")
            break
        if next_url == current:
            print("Next page URL is same as current, stopping to avoid loop.")
            break

        print(f"Page {page_num} -> next page: {next_url}")
        current = next_url
        page_num += 1

    # save combined results
    save_file('results.json', json.dumps(all_results, ensure_ascii=False, indent=2))
    print(f"Aggregated total results: {len(all_results)} (saved to results.json)")

    # Save individual aggregated items
    for i, r in enumerate(all_results, 1):
        save_file(f'result_{i}.json', json.dumps(r, ensure_ascii=False, indent=2))

    # Print concise summary for ALL items
    print('\nConcise summary for all aggregated items:')
    for i, r in enumerate(all_results, 1):
        rid = r.get('id') if isinstance(r, dict) else None
        name = r.get('name') or r.get('title') or r.get('headline') or '' if isinstance(r, dict) else ''
        # price can be nested
        price = ''
        if isinstance(r, dict):
            price = r.get('priceSummaryCzk') or r.get('priceCzk') or r.get('price') or ''
        locality = r.get('locality') if isinstance(r.get('locality'), dict) else {} if isinstance(r, dict) else {}
        city = ''
        lat = ''
        lon = ''
        if isinstance(locality, dict):
            city = locality.get('city')
            lat = locality.get('latitude')
            lon = locality.get('longitude')

        print('\n---')
        print(f"Result #{i}")
        print(f"id: {rid}")
        print(f"name: {name}")
        print(f"price: {price}")
        print(f"city: {city}")
        print(f"lat/lon: {lat} {lon}")

    return all_results


def main():
    parser = argparse.ArgumentParser(description='Scrape Sreality search page and extract embedded JSON results (all pages)')
    parser.add_argument('url', nargs='?', default=DEFAULT_URL, help='Search URL to scrape')
    args = parser.parse_args()

    try:
        results = scrape_all_pages(args.url)
        if results is None or not results:
            print("No results found.")
            sys.exit(1)
        else:
            print(f"Done. Total results found: {len(results)}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(2)


if __name__ == '__main__':
    main()
