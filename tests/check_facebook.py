from playwright.sync_api import sync_playwright

URL = "https://projectligtas.com/agos"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto(URL, wait_until="networkidle", timeout=120000)

    print("===== AGOS FLOOD MONITOR =====")
    print("TITLE:", page.title())
    print("URL:", page.url)

    print("\n===== PAGE DATA =====")

    text = page.locator("body").inner_text()

    # Print the useful portion of the page
    print(text[:12000])

    browser.close()
