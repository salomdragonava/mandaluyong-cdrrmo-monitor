import urllib.request
import json
import re
from datetime import datetime, timezone, timedelta

PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"
STATE_FILE = "pagasa_state.json"
ALERT_FILE = "pagasa_alert.txt"

PH_TZ = timezone(timedelta(hours=8))


def fetch_pagasa():
    request = urllib.request.Request(
        PAGASA_URL,
        headers={
            "User-Agent": "MandaluyongCDRRMO/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def get_warning_level(text):
    text_upper = text.upper()

    # Look specifically for Heavy Rainfall Warning information.
    if "HEAVY RAINFALL WARNING" not in text_upper:
        return "GREEN"

    # Check strongest level first.
    if re.search(r"\bRED\s+WARNING\b", text_upper):
        return "RED"

    if re.search(r"\bORANGE\s+WARNING\b", text_upper):
        return "ORANGE"

    if re.search(r"\bYELLOW\s+WARNING\b", text_upper):
        return "YELLOW"

    # PAGASA page explicitly says no Heavy Rainfall Warning.
    if "NO HEAVY RAINFALL WARNING" in text_upper:
        return "GREEN"

    return "GREEN"


def notification_interval(level):
    if level == "ORANGE":
        return 1

    if level == "YELLOW":
        return 2

    if level == "RED":
        return 1

    return 3


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


print("=" * 70)
print("PAGASA NCR MONITOR")
print("=" * 70)

try:

    page = fetch_pagasa()
    level = get_warning_level(page)

    now = datetime.now(PH_TZ)

    previous = load_state()

    previous_level = previous.get("level", "UNKNOWN")

    interval = notification_interval(level)

    print("PAGASA WARNING:", level)
    print("Previous:", previous_level)
    print("Report interval:", interval, "hour(s)")

    changed = (
        previous_level != "UNKNOWN"
        and previous_level != level
    )

    # --------------------------------------------------------
    # DETERMINE WHETHER A STATUS MESSAGE IS DUE
    # --------------------------------------------------------

    last_report = previous.get("last_report")

    hours_since_report = 999

    if last_report:
        try:
            previous_time = datetime.fromisoformat(last_report)
            hours_since_report = (
                now - previous_time
            ).total_seconds() / 3600
        except Exception:
            pass

    report_due = hours_since_report >= interval

    # Warning changes always trigger immediately.
    should_notify = changed or report_due

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    if should_notify:

        if level == "GREEN":
            icon = "🟢"
            description = "No active PAGASA Heavy Rainfall Warning"

        elif level == "YELLOW":
            icon = "🟡"
            description = "Flooding is possible"

        elif level == "ORANGE":
            icon = "🟠"
            description = "Flooding is threatening"

        else:
            icon = "🔴"
            description = "Severe flooding is expected"

        message = (
            f"{icon} PAGASA NCR WEATHER STATUS\n\n"
            f"Warning Level: {level}\n"
            f"{description}\n\n"
            f"Checked: {now.strftime('%Y-%m-%d %I:%M %p')}\n"
            f"Next routine report: every {interval} hour(s)"
        )

        with open(ALERT_FILE, "w", encoding="utf-8") as file:
            file.write(message)

        previous["last_report"] = now.isoformat()

        print("\n📨 PAGASA REPORT GENERATED")
        print(message)

    else:

        open(ALERT_FILE, "w", encoding="utf-8").close()

        print("\nNo PAGASA report due yet.")

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    previous["level"] = level
    previous["last_check"] = now.isoformat()
    previous["interval_hours"] = interval

    save_state(previous)

    print("\nPAGASA STATE SAVED")

except Exception as error:

    print("❌ PAGASA ERROR:", error)

    with open(ALERT_FILE, "w", encoding="utf-8") as file:
        file.write(
            "⚠️ PAGASA MONITOR ERROR\n\n"
            f"Unable to retrieve PAGASA NCR status.\n\n"
            f"Error: {error}"
        )
