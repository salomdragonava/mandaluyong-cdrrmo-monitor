from playwright.sync_api import sync_playwright

URL = "https://www.resilient-mandaluyong.org/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def handle_response(response):
        url = response.url

        # Show likely data/API requests
        if any(x in url.lower() for x in [
            "api",
            "json",
            "advis",
            "warning",
            "alert",
            "weather",
            "risk",
            "report"
        ]):
            print("NETWORK:", response.status, url)

    page.on("response", handle_response)

    page.goto(URL, wait_until="networkidle", timeout=120000)

    print("\n===== PAGE TITLE =====")
    print(page.title())

    print("\n===== PAGE URL =====")
    print(page.url)

    print("\n===== PAGE TEXT =====")
    print(page.locator("body").inner_text()[:10000])

    browser.close()
