from playwright.sync_api import sync_playwright

URL = "https://projectligtas.com/public-flood-risk"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def handle_response(response):
        url = response.url.lower()

        if any(word in url for word in [
            "api",
            "risk",
            "flood",
            "weather",
            "assess",
            "search",
            "geocod",
            "nominatim",
            "json"
        ]):
            print("NETWORK:", response.status, response.url)

    page.on("response", handle_response)

    page.goto(URL, wait_until="networkidle", timeout=120000)

    # Enter address
    address = page.locator(
        'input[placeholder*="Enter address"]'
    )

    address.fill("Highway Hills, Mandaluyong, Philippines")

    print("Entered address.")

    # Find the visible Search button
    search_button = page.locator("button").filter(
        has_text="Search"
    ).first

    print("Search button count:", search_button.count())

    if search_button.count() == 0:
        print("ERROR: Search button not found.")
        browser.close()
        raise SystemExit(1)

    print("Clicking Search...")
    search_button.click()

    page.wait_for_timeout(5000)

    print("\n===== AFTER SEARCH =====")
    print(page.locator("body").inner_text()[-5000:])

    # Find Assess Risk button
    assess_button = page.locator("button").filter(
        has_text="Assess Risk"
    ).first

    print("\nAssess Risk button count:", assess_button.count())

    if assess_button.count() == 0:
        print("ERROR: Assess Risk button not found.")
        browser.close()
        raise SystemExit(1)

    print("Clicking Assess Risk...")
    assess_button.click()

    page.wait_for_timeout(10000)

    print("\n===== FINAL RESULT =====")
    print(page.locator("body").inner_text()[-8000:])

    browser.close()
