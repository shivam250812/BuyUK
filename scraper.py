import csv
import random
import re
import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SENDER_EMAIL = os.getenv("SENDER_EMAIL") or os.getenv("SMTP_USERNAME")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD") or os.getenv("SMTP_PASSWORD")
if SENDER_PASSWORD:
    SENDER_PASSWORD = SENDER_PASSWORD.replace('"', '').replace("'", "").replace(" ", "")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL") or os.getenv("RECEIVER_EMAIL")

def send_email_notification(asin, seller, fetched_price, adjusted_price):
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
        print(f"[{asin}] Skipping email notification - missing credentials in .env file.")
        return

    recipient_list = [email.strip() for email in RECIPIENT_EMAIL.split(',') if email.strip()]

    msg = EmailMessage()
    msg.set_content(
        f"Alert! Buy Box lost for ASIN: {asin}.\n\n"
        f"Current Seller: {seller}\n"
        f"Fetched Price: {fetched_price}\n"
        f"Our Adjusted Price: {adjusted_price}\n\n"
        f"Link: https://www.amazon.co.uk/dp/{asin}"
    )
    msg["Subject"] = f"BuyBox UK Lost - ASIN: {asin}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(recipient_list)

    try:
        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.send_message(msg)
        print(f"[{asin}] Email notification sent successfully to: {', '.join(recipient_list)}")
    except Exception as e:
        print(f"[{asin}] Failed to send email: {e}")

def load_asins_from_csv(filename="input.csv"):
    asins = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row:
                    val = row[0].strip()
                    # Skip header if it exists
                    if val and val.upper() != "ASIN":
                        asins.append(val)
    except FileNotFoundError:
        print(f"Error: {filename} not found. Please create {filename} with your ASINs.")
    return asins

TARGET_SELLER = "Bargad Healthcare"

async def scrape_asin(context, asin, results):
    page = await context.new_page()
    
    # Block images to make it faster
    await page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
    
    url = f"https://www.amazon.co.uk/dp/{asin}"
    print(f"\n--- Scraping ASIN: {asin} ---")
    
    try:
        await page.goto(url, timeout=45000)
        # Random delay between 2 and 4 seconds
        await page.wait_for_timeout(random.randint(2000, 4000))
        
        # Check for CAPTCHA
        if "captcha" in page.url or await page.locator("form[action='/errors/validateCaptcha']").count() > 0:
            print(f"⚠️ [{asin}] Captcha detected! Please solve it in the browser.")
            # Wait until the url no longer contains captcha
            try:
                await page.wait_for_url(f"**/{asin}**", timeout=120000) 
                print(f"[{asin}] Captcha solved, continuing...")
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[{asin}] Timed out waiting for captcha to be solved.")
                results.append({"ASIN": asin, "Status": "Skip - Captcha unresolved", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
                return
        
        # Extract Price
        price_locator = page.locator('.a-price-whole').first
        if await price_locator.count() == 0:
            price_locator = page.locator('#priceblock_ourprice, #priceblock_dealprice').first
        
        if await price_locator.count() == 0:
            print(f"[{asin}] No price found -> Skip")
            results.append({"ASIN": asin, "Status": "Skip - No price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
            return
            
        price_text = (await price_locator.inner_text()).strip()
        # Clean price string
        clean_price_str = re.sub(r'[^\d.]', '', price_text)
        if not clean_price_str:
            print(f"[{asin}] Invalid price format -> Skip")
            results.append({"ASIN": asin, "Status": "Skip - Invalid price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
            return
            
        try:
            price_val = float(clean_price_str)
        except ValueError:
            print(f"[{asin}] Could not parse price '{price_text}' -> Skip")
            results.append({"ASIN": asin, "Status": "Skip - Could not parse price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
            return

        # Extract Seller
        seller_locator = page.locator('#sellerProfileTriggerId').first
        seller_text = ""
        if await seller_locator.count() > 0:
            seller_text = (await seller_locator.inner_text()).strip()
        else:
            merchant_info = page.locator('#merchant-info').first
            if await merchant_info.count() > 0:
                seller_text = (await merchant_info.inner_text()).strip()
            
        if not seller_text:
            print(f"[{asin}] No seller found (No buy box) -> Skip")
            results.append({"ASIN": asin, "Status": "Skip - No seller / No buy box", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
            return
        
        # Compare seller name
        if TARGET_SELLER.lower() in seller_text.lower():
            print(f"[{asin}] Seller is {TARGET_SELLER} -> No problem")
            results.append({
                "ASIN": asin, 
                "Status": "OK", 
                "Fetched Price": price_val, 
                "Seller": seller_text, 
                "New Price": "", 
                "Remark": "No problem"
            })
        else:
            new_price = price_val - 0.03
            print(f"[{asin}] Seller is '{seller_text}' -> Reducing price by 0.03")
            results.append({
                "ASIN": asin, 
                "Status": "Adjusted", 
                "Fetched Price": price_val, 
                "Seller": seller_text, 
                "New Price": round(new_price, 2), 
                "Remark": "Seller is other, adjusted price"
            })
            # Send email notification
            await asyncio.to_thread(send_email_notification, asin, seller_text, price_val, round(new_price, 2))
            
    except Exception as e:
        print(f"[{asin}] Error during scraping: {e}")
        results.append({"ASIN": asin, "Status": f"Error", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": str(e)})
        
    finally:
        await page.close()

import platform

async def scrape_amazon_async(asins):
    results = []
    
    print("Starting Playwright to scrape Amazon...")
    async with async_playwright() as p:
        
        # Auto-detect local Chrome to avoid Playwright install issues
        executable_path = None
        system = platform.system()
        if system == "Darwin":
            mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if os.path.exists(mac_path):
                executable_path = mac_path
        elif system == "Windows":
            win_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ]
            for path in win_paths:
                if os.path.exists(path):
                    executable_path = path
                    break
        
        launch_kwargs = {"headless": False}
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        
        # We need a setup page to set the pincode first
        setup_page = await context.new_page()
        # Block images on setup page too
        await setup_page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
        
        print("Setting delivery pincode to E1 6AN...")
        try:
            await setup_page.goto("https://www.amazon.co.uk/")
            await setup_page.wait_for_timeout(3000)
            
            location_link = setup_page.locator('#nav-global-location-popover-link')
            if await location_link.count() > 0:
                await location_link.click()
                await setup_page.wait_for_timeout(2000)
                
                pincode_input = setup_page.locator('#GLUXZipUpdateInput')
                if await pincode_input.count() > 0:
                    await pincode_input.fill("E1 6AN")
                    await setup_page.locator('#GLUXZipUpdate').click()
                    await setup_page.wait_for_timeout(2000)
                    
                    await setup_page.goto("https://www.amazon.co.uk/")
                    await setup_page.wait_for_timeout(2000)
            print("Pincode setup completed.")
        except Exception as e:
            print(f"Warning: Could not set pincode automatically: {e}")
            print("Please set the pincode manually in the browser window within the next 10 seconds.")
            await setup_page.wait_for_timeout(10000)
        finally:
            await setup_page.close()
            
        # Limit to 2 concurrent tabs
        semaphore = asyncio.Semaphore(2)
        
        async def sem_scrape(asin):
            async with semaphore:
                await scrape_asin(context, asin, results)
                
        tasks = [sem_scrape(asin) for asin in asins]
        await asyncio.gather(*tasks)
        
        await browser.close()
        
    # Write to CSV
    if results:
        csv_filename = 'amazon_prices.csv'
        fieldnames = ["ASIN", "Status", "Fetched Price", "Seller", "New Price", "Remark"]
        try:
            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"\nData successfully saved to {csv_filename}")
        except Exception as e:
            print(f"Failed to save CSV: {e}")
    else:
        print("\nNo data scraped.")

if __name__ == "__main__":
    asins_to_scrape = load_asins_from_csv("input.csv")
    if asins_to_scrape:
        print(f"Loaded {len(asins_to_scrape)} ASINs from input.csv")
        asyncio.run(scrape_amazon_async(asins_to_scrape))
    else:
        print("No ASINs found to scrape. Please ensure input.csv has data.")
