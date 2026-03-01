# ═══════════════════════════════════════════════════════════════
# Marketplace Sniper v5 — Multi-Site Burst Scraper
# Facebook Marketplace · Mzad Qatar · QatarSale
# ═══════════════════════════════════════════════════════════════

import asyncio
import json
import os
import random
import time
import hashlib
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Marketplace Sniper v5 — Scraper")

# ─── Config ──────────────────────────────────────────────────

DATA_DIR = Path("/app/data")
SEEN_IDS_FILE = DATA_DIR / "seen_ids.json"
COOKIES_FILE = DATA_DIR / "cookies.json"
MAX_SEEN_IDS = 2000  # FIFO cap

MARKETPLACE_URL = os.getenv("MARKETPLACE_URL", "https://www.facebook.com/marketplace/doha/vehicles")
PROXY_URL = os.getenv("PROXY_URL", "")
FILTER_URL = os.getenv("FILTER_URL", "http://filter:8002/filter")
SCRAPE_INTERVAL_MINS = int(os.getenv("SCRAPE_INTERVAL_MINS", "3"))

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# ─── State Management ────────────────────────────────────────

def load_seen_ids() -> list:
    if SEEN_IDS_FILE.exists():
        try:
            return json.loads(SEEN_IDS_FILE.read_text())
        except Exception:
            return []
    return []


def save_seen_ids(ids: list):
    # FIFO cap
    if len(ids) > MAX_SEEN_IDS:
        ids = ids[-MAX_SEEN_IDS:]
    SEEN_IDS_FILE.write_text(json.dumps(ids))


def load_cookies() -> dict:
    if COOKIES_FILE.exists():
        try:
            return json.loads(COOKIES_FILE.read_text())
        except Exception:
            return {"accounts": []}
    return {"accounts": []}


def pick_account(cookies_data: dict) -> Optional[dict]:
    accounts = [a for a in cookies_data.get("accounts", []) if a.get("cookies")]
    if not accounts:
        return None
    return random.choice(accounts)


# ─── Playwright Stealth Setup ────────────────────────────────

async def create_stealth_context(pw, account: Optional[dict] = None):
    """Create a Playwright browser context with stealth config."""
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--window-size=1920,1080",
    ]

    proxy_config = None
    if PROXY_URL:
        proxy_config = {"server": PROXY_URL}

    browser = await pw.chromium.launch(
        headless=True,
        args=browser_args,
    )

    ua = random.choice(UA_POOL)
    seed = account.get("fingerprint_seed", 42) if account else 42

    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="Asia/Qatar",
        proxy=proxy_config,
    )

    # Inject stealth scripts
    await context.add_init_script(f"""
        // Override navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});

        // Consistent canvas fingerprint
        const seed = {seed};
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const imgData = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imgData.data.length; i += 4) {{
                    imgData.data[i] ^= (seed & 0xFF);
                }}
                ctx.putImageData(imgData, 0, 0);
            }}
            return origToDataURL.apply(this, arguments);
        }};

        // Override plugins
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [1, 2, 3, 4, 5]
        }});

        // Override languages
        Object.defineProperty(navigator, 'languages', {{
            get: () => ['en-US', 'en']
        }});
    """)

    # Load cookies if available
    if account and account.get("cookies"):
        await context.add_cookies(account["cookies"])

    return browser, context


# ─── Site-Specific Extractors ────────────────────────────────

async def extract_facebook(page, url: str) -> list:
    """Extract listings from Facebook Marketplace."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(random.uniform(2, 4))

    page_text = await page.inner_text("body")
    if "Log in to Facebook" in page_text or "Log Into Facebook" in page_text:
        return [{"error": "COOKIES_EXPIRED"}]

    listings = []

    # Strategy 1: aria-label parsing
    cards = await page.query_selector_all('[aria-label*="QAR"], [aria-label*="qar"]')
    for card in cards[:50]:
        try:
            label = await card.get_attribute("aria-label")
            if not label:
                continue

            link_el = await card.query_selector("a[href*='/marketplace/item/']")
            href = await link_el.get_attribute("href") if link_el else ""

            # Parse: "Title, QAR Price, Location"
            parts = label.split(",")
            title = parts[0].strip() if parts else ""
            price = 0
            for p in parts:
                p = p.strip().upper()
                if "QAR" in p:
                    digits = "".join(c for c in p if c.isdigit())
                    if digits:
                        price = int(digits)
                        break

            item_id = ""
            if "/marketplace/item/" in href:
                item_id = href.split("/marketplace/item/")[-1].split("/")[0].split("?")[0]

            if title and price > 0:
                listings.append({
                    "id": item_id or hashlib.md5(f"{title}{price}".encode()).hexdigest()[:12],
                    "title": title,
                    "price": price,
                    "url": f"https://www.facebook.com{href}" if href.startswith("/") else href,
                    "phone": "",
                    "model_year": 0,
                    "mileage_km": 0,
                    "source": "facebook",
                })
        except Exception:
            continue

    # Strategy 2: __RELAY_STORE fallback
    if not listings:
        try:
            relay_data = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        if (s.textContent && s.textContent.includes('__RELAY_STORE')) {
                            const match = s.textContent.match(/"__RELAY_STORE"\\s*:\\s*(\\{.+?\\})\\s*[,}]/);
                            if (match) return match[1];
                        }
                    }
                    return null;
                }
            """)
            if relay_data:
                data = json.loads(relay_data)
                for key, val in data.items():
                    if isinstance(val, dict) and "listing_price" in str(val):
                        try:
                            title = val.get("marketplace_listing_title", "")
                            price_obj = val.get("listing_price", {})
                            price = int(price_obj.get("amount", 0)) if isinstance(price_obj, dict) else 0
                            lid = val.get("id", "")
                            if title and price > 0:
                                listings.append({
                                    "id": lid,
                                    "title": title,
                                    "price": price,
                                    "url": f"https://www.facebook.com/marketplace/item/{lid}",
                                    "phone": "",
                                    "model_year": 0,
                                    "mileage_km": 0,
                                    "source": "facebook",
                                })
                        except Exception:
                            continue
        except Exception:
            pass

    return listings


async def extract_mzad(page, url: str = "https://www.mzadqatar.com/en/cars") -> list:
    """Extract listings from Mzad Qatar."""
    # Ensure we are on the cars listing page
    if "vehicles" in url and "cars" not in url:
        url = "https://www.mzadqatar.com/en/cars"
        
    print(f"Navigating to Mzad: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # Random wait for hydration
        await asyncio.sleep(random.uniform(4, 7))
    except Exception as e:
        print(f"Mzad navigation failed: {e}")
        return []

    listings = []
    
    # Strategy 1: JSON Data Extraction (Client-side hydration)
    try:
        app_div = await page.query_selector("#app")
        if app_div:
            data_page = await app_div.get_attribute("data-page")
            if data_page:
                # We won't fully parse the complex JSON structure here as it changes often,
                # but we'll try to rely on DOM first. Leaving this placeholders for future expansion.
                pass 
    except:
        pass

    # Strategy 2: CSS Selectors
    # Mzad uses generic class structure.
    cards = await page.query_selector_all(".listing-card, .card, div[class*='d-flex'] > a[href*='/ad/']")

    print(f"Found {len(cards)} potential Mzad cards")

    for card in cards[:50]:
        try:
            # Title
            title_el = await card.query_selector("h2, h3, .title, [class*='title']")
            title = ""
            if title_el:
                title = (await title_el.inner_text()).strip()
            
            if not title:
                # Fallback: check image alt text
                img = await card.query_selector("img")
                if img:
                    title = await img.get_attribute("alt")
            
            if not title:
                continue

            # Price
            price = 0
            price_el = await card.query_selector(".price, [class*='price'], .currency")
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                digits = "".join(c for c in price_text if c.isdigit())
                if digits:
                    price = int(digits)

            # URL
            url_suffix = ""
            if await card.eval("node => node.tagName === 'A'"):
                url_suffix = await card.get_attribute("href")
            else:
                link_el = await card.query_selector("a")
                if link_el:
                    url_suffix = await link_el.get_attribute("href")
            
            if not url_suffix:
                continue
                
            if not url_suffix.startswith("http"):
                full_url = f"https://www.mzadqatar.com{url_suffix}"
            else:
                full_url = url_suffix

            item_id = hashlib.md5(f"{title}{price}{full_url}".encode()).hexdigest()[:12]

            listings.append({
                "id": item_id,
                "title": title,
                "price": price,
                "url": full_url,
                "source": "mzad",
            })
        except Exception as e:
            print(f"Error scraping Mzad card: {e}")
            continue

    return listings


async def extract_qatarsale(page, url: str = "https://www.qatarsale.com/en/products/cars_for_sale") -> list:
    """Extract listings from QatarSale."""
    # Ensure correct URL
    if "products/cars_for_sale" not in url:
        url = "https://www.qatarsale.com/en/products/cars_for_sale"

    print(f"Navigating to QatarSale: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Heavy Angular app requires significant wait
        await asyncio.sleep(random.uniform(6, 10))
        
        # Trigger lazy load
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"QatarSale navigation failed: {e}")
        return []

    listings = []

    # Updated selectors based on analysis: .list-card, .classic-card-wrapper
    cards = await page.query_selector_all(".list-card, .classic-card-wrapper, [class*='list-card']")
    
    print(f"Found {len(cards)} potential QatarSale cards")

    for card in cards[:50]:
        try:
            # Title: .title-section p text
            title_el = await card.query_selector(".title-section")
            title = (await title_el.inner_text()).strip().replace("\n", " ") if title_el else ""
            
            if not title:
                # Fallback to image alt
                img_el = await card.query_selector("img.prod-img")
                if img_el:
                    title = await img_el.get_attribute("alt")

            if not title:
                continue

            # Price: .product-discount-price-info .price.new, or generic .price
            price = 0
            price_el = await card.query_selector(".price.new, .price, [class*='price']")
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                digits = "".join(c for c in price_text if c.isdigit())
                if digits:
                    price = int(digits)

            # URL
            link_el = await card.query_selector("a.title-section, a[href*='/product/']")
            url_suffix = await link_el.get_attribute("href") if link_el else ""
            
            if url_suffix and not url_suffix.startswith("http"):
                full_url = f"https://www.qatarsale.com{url_suffix}"
            else:
                full_url = url_suffix

            item_id = hashlib.md5(f"{title}{price}{full_url}".encode()).hexdigest()[:12]

            if title:
                listings.append({
                    "id": item_id,
                    "title": title,
                    "price": price,
                    "url": full_url,
                    "phone": "",
                    "model_year": 0,
                    "mileage_km": 0,
                    "source": "qatarsale",
                })
        except Exception as e:
            print(f"Error scraping QatarSale card: {e}")
            continue

    return listings


# ─── Multi-Site Burst Scrape ─────────────────────────────────

SITE_EXTRACTORS = {
    "facebook": (MARKETPLACE_URL, extract_facebook),
    "mzad": ("https://www.mzadqatar.com/en/cars", extract_mzad),
    "qatarsale": ("https://www.qatarsale.com/en/products/cars_for_sale", extract_qatarsale),
}


async def burst_scrape(sites: list[str], forward_to_filter: bool = True) -> dict:
    """Run a single burst scrape across specified sites."""
    from playwright.async_api import async_playwright

    cookies_data = load_cookies()
    account = pick_account(cookies_data)
    seen_ids = load_seen_ids()

    all_listings = []
    errors = []

    pw = None
    browser = None
    context = None

    try:
        pw = await async_playwright().start()
        browser, context = await create_stealth_context(pw, account)
        page = await context.new_page()

        for site_name in sites:
            if site_name not in SITE_EXTRACTORS:
                errors.append(f"Unknown site: {site_name}")
                continue

            url, extractor = SITE_EXTRACTORS[site_name]
            print(f"[SCRAPE] {site_name}: {url}")

            try:
                site_listings = await extractor(page, url)

                # Check for cookie expiration
                if site_listings and isinstance(site_listings[0], dict) and site_listings[0].get("error") == "COOKIES_EXPIRED":
                    errors.append(f"{site_name}: COOKIES_EXPIRED")
                    continue

                # Dedup
                new_listings = []
                for item in site_listings:
                    if item["id"] not in seen_ids:
                        new_listings.append(item)
                        seen_ids.append(item["id"])

                all_listings.extend(new_listings)
                print(f"[SCRAPE] {site_name}: {len(site_listings)} total, {len(new_listings)} new")

            except Exception as e:
                errors.append(f"{site_name}: {str(e)}")
                print(f"[ERR] {site_name}: {e}")

                # Self-healing: reset page on error
                try:
                    await page.close()
                    page = await context.new_page()
                except Exception:
                    pass

        save_seen_ids(seen_ids)

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    # Forward to C++ filter
    filter_result = None
    if forward_to_filter and all_listings:
        try:
            resp = requests.post(
                FILTER_URL,
                json=all_listings,
                timeout=10,
            )
            filter_result = resp.json()
            print(f"[FILTER] Response: {filter_result.get('stats', {})}")
        except Exception as e:
            errors.append(f"filter: {str(e)}")

    return {
        "scraped": len(all_listings),
        "sites": sites,
        "errors": errors,
        "filter_result": filter_result,
    }


# ─── Background Scheduler ───────────────────────────────────

async def auto_scraper_loop():
    """Infinite loop that runs the burst scrape periodically."""
    print(f"[AUTO] Sniper Scheduler Active. Interval: {SCRAPE_INTERVAL_MINS} minutes.")
    
    # Wait for services to be ready
    await asyncio.sleep(10)
    
    while True:
        try:
            print("[AUTO] Starting scheduled burst scrape...")
            sites = ["mzad", "qatarsale"]
            # We don't do facebook every time to avoid bans unless cookies are configured
            if load_cookies().get("accounts"):
                sites.append("facebook")
                
            result = await burst_scrape(sites)
            print(f"[AUTO] Scrape finished: {result.get('scraped', 0)} new listings found.")
        except Exception as e:
            print(f"[AUTO] Scheduler error: {e}")
        
        jitter = random.randint(-60, 60)
        wait_seconds = (SCRAPE_INTERVAL_MINS * 60) + jitter
        print(f"[AUTO] Sleeping for {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)


@app.on_event("startup")
async def start_scheduler():
    asyncio.create_task(auto_scraper_loop())


# ─── API Endpoints ───────────────────────────────────────────

@app.get("/scrape")
async def scrape(
    sites: str = Query(default="facebook,mzad,qatarsale", description="Comma-separated site list"),
    dry_run: bool = Query(default=False),
):
    if dry_run:
        mock = [
            {
                "id": "mock_001",
                "title": "Land Cruiser 300 VXR 2024 Urgent Sale",
                "price": 260000,
                "url": "https://example.com/listing/mock001",
                "phone": "+974 5555 1234",
                "model_year": 2024,
                "mileage_km": 15000,
                "source": "mzad",
            },
            {
                "id": "mock_002",
                "title": "Nissan Patrol 2023 Low Mileage",
                "price": 180000,
                "url": "https://example.com/listing/mock002",
                "phone": "+974 5555 5678",
                "model_year": 2023,
                "mileage_km": 25000,
                "source": "qatarsale",
            },
        ]
        # Forward mock to filter too
        try:
            resp = requests.post(FILTER_URL, json=mock, timeout=10)
            return JSONResponse(content={"mock": True, "listings": mock, "filter": resp.json()})
        except Exception as e:
            return JSONResponse(content={"mock": True, "listings": mock, "filter_error": str(e)})

    site_list = [s.strip() for s in sites.split(",") if s.strip()]
    result = await burst_scrape(site_list)
    return JSONResponse(content=result)


@app.get("/health")
async def health():
    return JSONResponse(content={"status": "ok", "service": "scraper-v5"})


@app.post("/refresh-cookies")
async def refresh_cookies(account_id: int = Query(...)):
    """Attempt self-healing cookie refresh via Playwright login."""
    from playwright.async_api import async_playwright

    email = os.getenv(f"FB_EMAIL_{account_id}")
    password = os.getenv(f"FB_PASS_{account_id}")

    if not email or not password:
        return JSONResponse(
            status_code=400,
            content={"error": f"FB_EMAIL_{account_id} or FB_PASS_{account_id} not set"},
        )

    pw = None
    browser = None
    context = None

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=random.choice(UA_POOL),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        await page.fill("#email", email)
        await page.fill("#pass", password)
        await page.click('[name="login"]')
        await asyncio.sleep(5)

        body = await page.inner_text("body")

        if "Enter the code" in body or "two-factor" in body.lower():
            return JSONResponse(
                status_code=403,
                content={"error": "2FA_REQUIRED", "account_id": account_id},
            )

        if "Log in to Facebook" in body:
            return JSONResponse(
                status_code=401,
                content={"error": "LOGIN_FAILED", "account_id": account_id},
            )

        # Extract fresh cookies
        fresh_cookies = await context.cookies("https://www.facebook.com")

        # Update cookies.json
        cookies_data = load_cookies()
        for acc in cookies_data.get("accounts", []):
            if acc.get("id") == account_id:
                acc["cookies"] = [
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"],
                        "secure": c.get("secure", True),
                        "httpOnly": c.get("httpOnly", False),
                    }
                    for c in fresh_cookies
                    if "facebook.com" in c.get("domain", "")
                ]
                break

        COOKIES_FILE.write_text(json.dumps(cookies_data, indent=2))

        return JSONResponse(content={
            "status": "refreshed",
            "account_id": account_id,
            "cookies_count": len(fresh_cookies),
        })

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


# ─── Main ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")
