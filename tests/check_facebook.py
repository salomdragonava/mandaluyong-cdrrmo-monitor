from playwright.sync_api import sync_playwright

URL = "https://www.resilient-mandaluyong.org/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto(URL, wait_until="networkidle", timeout=120000)

    print("===== LINKS ON MENCHIE =====")

    links = page.locator("a").all()

    for link in links:
        try:
            text = link.inner_text().strip()
            href = link.get_attribute("href")

            if text or href:
                print(f"TEXT: {text}")
                print(f"URL:  {href}")
                print("---")
        except:
            pass

    browser.close()
