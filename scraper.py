#!/usr/bin/env python3

import argparse
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys

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


def extract_and_save(url):
    html = fetch_page(url)
    page_raw = save_file('page_raw.html', html)
    soup = BeautifulSoup(html, 'html.parser')
    page_pretty = save_file('page_pretty.html', soup.prettify())
    print(f"Saved raw HTML: {page_raw}\nSaved prettified HTML: {page_pretty}")

    # find JSON candidates
    candidates = find_json_candidates(soup)
    print(f"Found {len(candidates)} JSON-like <script> candidate(s)")
    for idx, (i, content) in enumerate(candidates[:10]):
        path = save_file(f'script_candidate_{i}.txt', content)
        print(f"Saved script candidate #{idx+1} to: {path}")

    # extract JSON objects
    extracted_objs = []
    for i, content in candidates:
        objs = try_extract_json_from_text(content)
        if objs:
            extracted_objs.extend(objs)
    # save extracted objects
    for k, obj in enumerate(extracted_objs):
        path = save_file(f'extracted_{k}.json', json.dumps(obj, ensure_ascii=False, indent=2))
        print(f"Saved extracted JSON #{k+1} to: {path}")

    # Try to find results lists inside extracted objects
    results_lists = []
    for obj in extracted_objs:
        results_lists.extend(find_results_lists(obj))

    # Fallback: search extracted files we saved earlier
    if not results_lists:
        for fname in os.listdir(out_dir):
            if fname.startswith('extracted_') and fname.endswith('.json'):
                try:
                    with open(os.path.join(out_dir, fname), 'r', encoding='utf-8') as f:
                        obj = json.load(f)
                    results_lists.extend(find_results_lists(obj))
                    if results_lists:
                        break
                except Exception:
                    continue

    if not results_lists:
        print("No 'results' arrays found in extracted JSON objects. Check 'script_candidate_*.txt' and 'page_raw.html'.")
        return None

    # take the first results list
    results = results_lists[0]
    save_file('results.json', json.dumps(results, ensure_ascii=False, indent=2))
    print(f"Found results list with {len(results)} items (saved to results.json)")

    # Save individual items and print concise summary
    out_items = []
    for i, r in enumerate(results, 1):
        save_file(f'result_{i}.json', json.dumps(r, ensure_ascii=False, indent=2))
        rid = r.get('id')
        name = r.get('name') or r.get('title') or r.get('headline') or ''
        price = r.get('priceSummaryCzk') or r.get('priceCzk') or r.get('price') or ''
        locality = r.get('locality') if isinstance(r.get('locality'), dict) else {}
        city = locality.get('city')
        lat = locality.get('latitude')
        lon = locality.get('longitude')
        out_items.append({'id': rid, 'name': name, 'price': price, 'city': city, 'lat': lat, 'lon': lon})

    # print concise summary of first N (up to 10)
    nprint = min(10, len(out_items))
    print(f"Printing concise summary for first {nprint} items:")
    for i, it in enumerate(out_items[:nprint], 1):
        print('\n---')
        print(f"Result #{i}")
        print(f"id: {it['id']}")
        print(f"name: {it['name']}")
        print(f"price: {it['price']}")
        print(f"city: {it['city']}")
        print(f"lat/lon: {it['lat']} {it['lon']}")

    return results


def main():
    parser = argparse.ArgumentParser(description='Scrape Sreality search page and extract embedded JSON results')
    parser.add_argument('url', nargs='?', default=DEFAULT_URL, help='Search URL to scrape')
    args = parser.parse_args()

    try:
        results = extract_and_save(args.url)
        if results is None:
            sys.exit(1)
        else:
            print(f"Done. Total results found: {len(results)}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(2)


if __name__ == '__main__':
    main()
