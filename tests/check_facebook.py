from playwright.sync_api import sync_playwright

URL = "https://www.resilient-mandaluyong.org/resources"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto(URL, wait_until="networkidle", timeout=120000)

    print("===== RESOURCES PAGE =====")
    print("TITLE:", page.title())
    print("URL:", page.url)

    print("\n===== PAGE TEXT =====")
    print(page.locator("body").inner_text()[:15000])

    print("\n===== LINKS =====")

    for link in page.locator("a").all():
        try:
            text = link.inner_text().strip()
            href = link.get_attribute("href")

            if text or href:
                print(f"{text} -> {href}")
        except:
            pass

    browser.close()
