from playwright.sync_api import sync_playwright

URL = "https://projectligtas.com/public-flood-risk"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def handle_response(response):
        url = response.url.lower()

        # Print requests that look like data/API requests
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

    print("===== PAGE LOADED =====")
    print("TITLE:", page.title())
    print("URL:", page.url)

    # Find inputs
    print("\n===== INPUTS =====")

    for i, element in enumerate(page.locator("input").all()):
        try:
            print(
                i,
                "type=", element.get_attribute("type"),
                "name=", element.get_attribute("name"),
                "placeholder=", element.get_attribute("placeholder")
            )
        except:
            pass

    browser.close()
