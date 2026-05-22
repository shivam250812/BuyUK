import csv
import random
import re
import asyncio
from playwright.async_api import async_playwright

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
            
    except Exception as e:
        print(f"[{asin}] Error during scraping: {e}")
        results.append({"ASIN": asin, "Status": f"Error", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": str(e)})
        
    finally:
        await page.close()

async def scrape_amazon_async(asins):
    results = []
    
    print("Starting Playwright to scrape Amazon...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        
        # Set default currency cookie for Amazon UK
        await context.add_cookies([{
            'name': 'i18n-prefs',
            'value': 'GBP',
            'domain': '.amazon.co.uk',
            'path': '/'
        }])
        
        # We need a setup page to set the postcode first
        setup_page = await context.new_page()
        # Block images on setup page too
        await setup_page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
        
        print("Setting delivery postcode to SW1A 1AA...")
        try:
            await setup_page.goto("https://www.amazon.co.uk/")
            await setup_page.wait_for_timeout(3000)
            
            # Accept cookies if banner appears
            cookie_accept = setup_page.locator('#sp-cc-accept')
            if await cookie_accept.count() > 0:
                print("Accepting cookies...")
                await cookie_accept.click()
                await setup_page.wait_for_timeout(1000)
            
            location_link = setup_page.locator('#nav-global-location-popover-link')
            if await location_link.count() > 0:
                await location_link.click()
                
                # Wait dynamically for the postcode input to load
                try:
                    await setup_page.wait_for_selector('#GLUXZipUpdateInput', timeout=6000)
                except Exception:
                    pass
                
                pincode_input = setup_page.locator('#GLUXZipUpdateInput')
                if await pincode_input.count() > 0:
                    await pincode_input.fill("SW1A 1AA")
                    await setup_page.locator('#GLUXZipUpdate').click()
                    await setup_page.wait_for_timeout(3000)
                    
                    # Try to click Done/Continue button if it appears
                    done_btn = setup_page.locator('#GLUXConfirmClose, input[name="glowDoneButton"]').first
                    if await done_btn.count() > 0:
                        try:
                            if await done_btn.is_visible(timeout=2000):
                                await done_btn.click()
                                await setup_page.wait_for_timeout(2000)
                        except Exception:
                            pass
                    
                    await setup_page.goto("https://www.amazon.co.uk/")
                    await setup_page.wait_for_timeout(2000)
            print("Postcode setup completed.")
        except Exception as e:
            print(f"Warning: Could not set postcode automatically: {e}")
            print("Please set the postcode manually in the browser window within the next 10 seconds.")
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
