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

    # Enter Mandaluyong location
    address = page.locator(
        'input[placeholder*="Enter address"]'
    )

    address.fill("Highway Hills, Mandaluyong, Philippines")

    print("Entered address.")

    # First search for the address
    search_button = page.get_by_role("button", name="Search", exact=True)

    print("Clicking Search...")
    search_button.click()

    # Allow geocoding/search to complete
    page.wait_for_timeout(5000)

    print("\n===== AFTER SEARCH =====")
    print(page.locator("body").inner_text()[-5000:])

    # Now click Assess Risk
    assess_button = page.get_by_role(
        "button",
        name="Assess Risk",
        exact=True
    )

    print("\nClicking Assess Risk...")
    assess_button.click()

    # Allow risk assessment to complete
    page.wait_for_timeout(10000)

    print("\n===== FINAL RESULT =====")
    print(page.locator("body").inner_text()[-8000:])

    browser.close()
