import httpx
import re

# Test DuckDuckGo lite
r = httpx.get(
    "https://lite.duckduckgo.com/lite/",
    params={"q": "geography facts interesting"},
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"},
    follow_redirects=True,
)
print(f"Status: {r.status_code}, len: {len(r.text)}")

# Try different patterns
links = re.findall(r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.DOTALL)
print(f"result-link pattern: {len(links)}")

snippets = re.findall(r'class="result-snippet">(.*?)</td>', r.text, re.DOTALL)
print(f"result-snippet pattern: {len(snippets)}")

# Generic link finder
all_links = re.findall(r'href="(https?://[^"]+)"', r.text)
external = [l for l in all_links if "duckduckgo" not in l]
print(f"External links: {len(external)}")
for l in external[:5]:
    print(f"  {l}")

# Print a chunk to see structure
if len(r.text) > 2000:
    print("\n--- HTML sample (2000-4000) ---")
    print(r.text[2000:4000])
