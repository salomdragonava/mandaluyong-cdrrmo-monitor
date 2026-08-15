from playwright.sync_api import sync_playwright

URL = "https://www.facebook.com/MandaluyongCDRRMO/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)

    print("TITLE:", page.title())
    print("URL:", page.url)
    print("PAGE CONTENT:")
    print(page.locator("body").inner_text()[:5000])

    browser.close()
