import os
import requests
import asyncio
from fastapi import FastAPI, BackgroundTasks, Request
from telegram import Bot
import uvicorn
import logging
import sys

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

import generator
from sales_bot import setup_sales_bot

app = FastAPI(title="Doha Arbitrage Cloud Engine")

VIP_BOT_TOKEN = os.getenv("VIP_BOT_TOKEN", "")
VIP_GROUP_ID = os.getenv("VIP_GROUP_ID", "-1003837108549")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "7340549633")

sales_bot_app = None

@app.on_event("startup")
async def startup_event():
    logging.info("Starting Cloud Engine...")
    
    # DNS Debug Check
    try:
        import socket
        ip = socket.gethostbyname("api.telegram.org")
        logging.info(f"DNS Check: api.telegram.org resolved to {ip}")
    except Exception as e:
        logging.error(f"DNS Check FAILED: {e}")

    global sales_bot_app
    sales_bot_app = await setup_sales_bot()
    
    # Run webhook setup in background
    asyncio.create_task(setup_webhooks_persistent())
    logging.info("Engine startup sequence initiated.")

async def setup_webhooks_persistent():
    """Retries setting the webhook until success or max retries reached."""
    delay = 5
    max_retries = 10
    
    for i in range(max_retries):
        if not sales_bot_app: break
        
        await asyncio.sleep(delay)
        space_url = os.getenv("SPACE_URL", "https://zyadthecreator-doacck.hf.space")
        webhook_url = f"{space_url}/tg-webhook"
        
        try:
            print(f"[INFO] Webhook Setup (Attempt {i+1}): Target {webhook_url}")
            await sales_bot_app.bot.set_webhook(url=webhook_url)
            print("[INFO] Sales Bot Webhook set successfully.")
            return
        except Exception as e:
            print(f"[TG WEBHOOK SET ERR] Attempt {i+1} failed: {e}")
            delay = min(delay * 2, 60) # Exponential backoff
            
    print("[ERROR] Failed to set Sales Bot webhook after many retries.")

@app.on_event("shutdown")
async def shutdown_event():
    global sales_bot_app
    if sales_bot_app:
        print("[INFO] Stopping Sales Bot...")
        if hasattr(sales_bot_app, "updater") and sales_bot_app.updater:
            await sales_bot_app.updater.stop()
        await sales_bot_app.stop()
        await sales_bot_app.shutdown()

async def broadcast_deal(deal: dict):
    title = deal.get("title", "Car")
    price = deal.get("price", "0")
    year = deal.get("model_year", 0)
    mileage = deal.get("mileage_km", 0)
    url = deal.get("url", "")
    
    # Generate watermark image
    image_url = generator.generate_watermark_url(url, price, title)
    print(f"[IMAGE] {image_url}")
    
    # Generate caption
    caption = generator.generate_caption(title, price, year, mileage, url)
    print(f"[CAPTION] \n{caption}")
    
    # Post to VIP Telegram
    if VIP_BOT_TOKEN and VIP_GROUP_ID:
        logging.info(f"Preparing to broadcast to Telegram group: {VIP_GROUP_ID}")
        # Retry with backoff for broadcast too
        for i in range(3):
            try:
                # Initialize bot inside the loop to catch connection errors
                bot = Bot(token=VIP_BOT_TOKEN)
                await bot.initialize()
                try:
                    await bot.send_photo(chat_id=VIP_GROUP_ID, photo=image_url, caption=caption)
                    logging.info("VIP Telegram: Posted photo successfully.")
                    break
                except Exception as e:
                    logging.error(f"VIP Telegram photo failed (Attempt {i+1}): {e}")
                    await bot.send_message(chat_id=VIP_GROUP_ID, text=f"{caption}")
                    logging.info("VIP Telegram: Sent as text backup.")
                    break
                finally:
                    await bot.shutdown()
            except Exception as e:
                logging.error(f"VIP TG CONNECT ERR (Attempt {i+1}): {e}")
                await asyncio.sleep(2)
            
    # Send Manual Posting Package to Admin
    if VIP_BOT_TOKEN and ADMIN_CHAT_ID:
        logging.info(f"Sending Manual Posting Package to Admin: {ADMIN_CHAT_ID}")
        try:
            bot = Bot(token=VIP_BOT_TOKEN)
            await bot.initialize()
            admin_msg = f"📸 **MANUAL POSTING PACKAGE** 📸\n\n**Platform:** Instagram / Facebook\n\n**Caption:**\n{caption}\n\n**Image URL:**\n{image_url}"
            try:
                await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=image_url, caption=admin_msg)
                logging.info("Admin: Manual package sent successfully.")
            except Exception as e:
                logging.error(f"Admin package failed: {e}")
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg)
            finally:
                await bot.shutdown()
        except Exception as e:
            logging.error(f"Admin connection failed: {e}")

@app.post("/webhook/7a558860-2034-4944-9398-ec29bf5ee52c")
async def receive_deal_legacy(request: Request, background_tasks: BackgroundTasks):
    return await receive_deal(request, background_tasks)

@app.post("/tg-webhook")
async def tg_webhook(request: Request):
    if not sales_bot_app:
        return {"status": "bot_disabled"}
    try:
        data = await request.json()
        from sales_bot import handle_webhook_update
        await handle_webhook_update(data, sales_bot_app)
        return {"status": "ok"}
    except Exception as e:
        print(f"[TG WEBHOOK ENDPOINT ERR] {e}")
        return {"status": "error"}

@app.post("/webhook/new-sniper-deal")
async def receive_deal(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        print(f"[WEBHOOK] Received payload: {data}")
        # Could be a list of deals or a single deal
        deals = data if isinstance(data, list) else [data]
            
        for deal in deals:
            background_tasks.add_task(broadcast_deal, deal)
            
        return {"status": "accepted", "count": len(deals)}
    except Exception as e:
        print(f"[WEBHOOK ERR] {e}")
        return {"status": "error", "message": str(e)}

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def health():
    return "<h1>🚀 Doha Arbitrage Cloud Engine is LIVE!</h1><p>Waiting for deals...</p><br><a href='/debug/network'>[Run Network Diagnostic]</a>"

@app.get("/debug/network")
async def debug_network():
    results = {}
    test_urls = [
        "https://www.google.com",
        "https://api.telegram.org",
        "https://api.cloudinary.com",
        "https://graph.facebook.com"
    ]
    
    import socket
    dns_results = {}
    for host in ["google.com", "api.telegram.org", "api.cloudinary.com"]:
        try:
            dns_results[host] = socket.gethostbyname(host)
        except Exception as e:
            dns_results[host] = f"FAILED: {e}"
            
    for url in test_urls:
        try:
            r = requests.get(url, timeout=5)
            results[url] = f"SUCCESS (Status: {r.status_code})"
        except Exception as e:
            results[url] = f"FAILED: {e}"
            
    # Direct IP Test
    ip_test = {}
    try:
        # api.telegram.org common IP
        r = requests.get("https://149.154.167.220", verify=False, timeout=5)
        ip_test["149.154.167.220 (Telegram)"] = f"REACHABLE (Status: {r.status_code})"
    except Exception as e:
        ip_test["149.154.167.220 (Telegram)"] = f"UNREACHABLE: {e}"

    return {
        "dns": dns_results,
        "http_checks": results,
        "ip_direct_checks": ip_test,
        "environment": {
            "SPACE_ID": os.getenv("SPACE_ID"),
            "USER": os.getenv("USER")
        }
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
