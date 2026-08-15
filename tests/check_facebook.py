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
            "ajax",
            "json"
        ]):
            print("NETWORK:", response.status, response.url)

    page.on("response", handle_response)

    page.goto(URL, wait_until="networkidle", timeout=120000)

    # Enter a Mandaluyong location
    address = page.locator(
        'input[placeholder*="Enter address"]'
    )

    address.fill("Highway Hills, Mandaluyong, Philippines")

    print("Entered address.")

    # Show available buttons
    print("\n===== BUTTONS =====")

    for button in page.locator("button").all():
        try:
            print("BUTTON:", button.inner_text().strip())
        except:
            pass

    # Try the button containing "risk"
    risk_button = page.get_by_text("Check Risk", exact=True)

    if risk_button.count() > 0:
        print("\nClicking Check Risk...")
        risk_button.click()
    else:
        print("\nCheck Risk button not found.")

    # Give the page time to process the assessment
    page.wait_for_timeout(10000)

    print("\n===== RESULT PAGE TEXT =====")
    print(page.locator("body").inner_text()[:10000])

    browser.close()
