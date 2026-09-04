# weather_agent.py KANW NM 89.1FM Weather
import os
import re
import html
import smtplib
from pathlib import Path

import requests
import argparse
import random
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from dateutil.parser import parse as parse_date
from dateutil.rrule import rrulestr
from dotenv import load_dotenv
from icalendar import Calendar
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

def make_session():
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "REPORT"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

CLOSINGS = [
    "Enjoy the music on KANW, and have a wonderful day.",
    "Stay safe, stay informed, and enjoy New Mexico Music on KANW.",
    "Wherever your travels take you across New Mexico, have a great day with KANW.",
    "Thanks for listening to KANW—your home for New Mexico Music.",
    "Have a safe day, and thank you for making KANW part of it.",
    "From all of us at KANW, enjoy today's forecast and the music ahead.",
    "Here's wishing you clear skies and great New Mexico Music on KANW.",
    "Until next time, enjoy the weather and keep it tuned to KANW.",
]

SESSION = make_session()

TZ = ZoneInfo("America/Denver")
STATE_FILE = "state.txt"
CACHE = {}

LOCATIONS = {
    "Albuquerque": (35.0844, -106.6504),
    "Santa Fe": (35.6870, -105.9378),
    "Grants": (35.1473, -107.8514),
    "Gallup": (35.5281, -108.7426),
    "Las Vegas": (35.5942, -105.2228),
    "Santa Rosa": (34.9387, -104.6825),
}

METRO_AREAS = {
    "Valley": (35.1030, -106.6700),
    "Heights": (35.1100, -106.5300),
}

LISTENING_AREA = [
    "Bernalillo",
    "Sandoval",
    "Santa Fe",
    "Valencia",
    "Torrance",
    "San Miguel",
]

HEADERS = {
    "User-Agent": "burquebro-weather-agent, paul@paulroybal.com",
    "Accept": "application/geo+json",
}

def get_json(url):
    if url in CACHE:
        return CACHE[url]

    r = SESSION.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    CACHE[url] = data
    return data


def get_forecast_urls(lat, lon):
    key = f"points:{lat},{lon}"

    if key in CACHE:
        return CACHE[key]

    point = get_json(f"https://api.weather.gov/points/{lat},{lon}")
    props = point["properties"]
    result = props["forecast"], props["forecastHourly"]
    CACHE[key] = result
    return result


def get_forecast_periods(lat, lon):
    forecast_url, _ = get_forecast_urls(lat, lon)
    forecast = get_json(forecast_url)
    return forecast["properties"]["periods"]


def get_current_temp(lat, lon):
    _, hourly_url = get_forecast_urls(lat, lon)
    hourly = get_json(hourly_url)
    period = hourly["properties"]["periods"][0]
    return period.get("temperature")


def pick_city_period(periods):
    now = datetime.now(TZ)
    want_daytime = now.hour < 15

    for p in periods:
        if p.get("isDaytime") == want_daytime:
            return p

    return periods[0]


def get_nm_alerts():
    try:
        data = get_json(
            "https://api.weather.gov/alerts/active?area=NM"
        )

        features = data.get("features", [])

        if not features:
            return []

        now = datetime.now(TZ)

        alerts = []

        for feature in features:
            props = feature.get("properties", {})

            headline = props.get("headline", "").strip()
            area_desc = props.get("areaDesc", "").strip()

            # ---------------------------------
            # Ignore alerts outside our
            # listening area
            # ---------------------------------

            if not any(
                area.lower() in area_desc.lower()
                for area in LISTENING_AREA
            ):
                continue

            # ---------------------------------
            # Check expiration/end time
            # ---------------------------------

            end_text = (
                props.get("ends")
                or props.get("expires")
            )

            if end_text:
                try:
                    end_time = parse_date(end_text)

                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=TZ)
                    else:
                        end_time = end_time.astimezone(TZ)

                    if end_time <= now:
                        continue

                except Exception as ex:
                    print(
                        f"Could not parse alert expiration: {ex}"
                    )

            if headline:
                alerts.append(headline)

        return alerts

    except Exception as ex:
        print(f"Weather alert error: {ex}")
        return []

def clean_afd_text(text):
    text = text or ""

    text = re.sub(r"\$\$", "", text)
    text = re.sub(r"&&", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()

    return text


def get_latest_afd_text():
    products = get_json("https://api.weather.gov/products/types/AFD/locations/ABQ")

    graph = products.get("@graph", [])
    if not graph:
        raise RuntimeError("No AFD products found")

    latest_id = graph[0]["id"]
    product = get_json(f"https://api.weather.gov/products/{latest_id}")
    text = product.get("productText", "")

    match = re.search(
        r"\.SYNOPSIS\.\.\.\s*(.*?)(?=\n\.[A-Z]|\n&&|\Z)",
        text,
        re.DOTALL,
    )

    if not match:
        match = re.search(
            r"\.DISCUSSION\.\.\.\s*(.*?)(?=\n\.[A-Z]|\n&&|\Z)",
            text,
            re.DOTALL,
        )

    if match:
        summary = match.group(1).strip()
    else:
        summary = text[:1800].strip()

    summary = clean_afd_text(summary)

    sentences = re.split(r"(?<=[.!?])\s+", summary)

    skip_words = [
        "aviation",
        "taf",
        "zulu",
        "hrrr",
        "nam",
        "gfs",
        "ecmwf",
        "pwat",
        "h5",
        "h7",
        "700mb",
        "500mb",
        "model",
        "guidance",
        "forecast package",
    ]

    good = []

    for sentence in sentences:
        s = sentence.strip()
        low = s.lower()

        if len(s) < 30:
            continue

        if any(word in low for word in skip_words):
            continue

        good.append(s)

        if len(good) >= 4:
            break

    if not good:
        raise RuntimeError("No usable AFD sentences found")

    return " ".join(good)


def ask_ollama(prompt):
    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

    url = f"{ollama_url}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 300,
        },
    }

    r = SESSION.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["response"].strip()
def rewrite_statewide_with_ollama(facts):
    statewide_data = build_statewide_weather_data()

    prompt = f"""
    Do not include an introduction to your report.
    
      
    You are writing a generalized statewide New Mexico weather forecast for radio.

    Use the National Weather Service information below to create a concise,
    natural-sounding statewide forecast.

    Do not list cities one by one. Instead, compare regions and identify the
    overall weather pattern across New Mexico.

    Do not confuse highs with low temperatures.
    
    Be sure to use complete sentences.
    
    Write one short paragraph of approximately 50 to 75 words.

    This forecast will be read live on radio, so prioritize only the
    most important weather information.
    
    Focus on:
    - the dominant statewide weather pattern
    - the areas most affected
    - significant hazards
    - major temperature differences
    
    Do not repeat similar weather conditions for multiple regions.
    Do not list individual city forecasts.
    Do not include minor details when a broader regional description
    will communicate the same information.
    Use short, natural sentences that are easy to read aloud.

    Discuss when relevant and significantly more detailed:
    - temperature differences
    - thunderstorm coverage and timing
    - areas with the highest precipitation chances
    - wind
    - significant changes tonight or tomorrow
    - hazards mentioned by the National Weather Service

    Use varied conversational language suitable for a radio announcer.
    Avoid repeatedly saying "expect", "look for", or "there is a chance".
    Do not invent weather conditions that are not supported by the data.
    Do not include headings or bullet points.
    Never state rainfall amounts unless an amount is explicitly
    provided in the source information.
    
    Do not infer overnight low temperatures from daytime forecasts.
    
    Be geographically accurate when describing New Mexico regions.
    
    Do not describe a weather system, such as high pressure or
    low pressure, unless it is explicitly mentioned in the
    National Weather Service information.
    
    Prefer regional descriptions over listing individual cities.
    Write one concise paragraph 100 words.
    Do not include "WEATHER ALERT" section.
    
    NATIONAL WEATHER SERVICE KEY MESSAGES:
    {facts}
    
    REPRESENTATIVE NEW MEXICO FORECASTS:
    {statewide_data}
    """

    try:
        text = ask_ollama(prompt)

        bad_phrases = [
            "here is",
            "facts provided",
            "national weather service key messages",
            "as an ai",
            "i cannot",
        ]

        if len(text) < 60:
            raise RuntimeError("Ollama response too short")

        if any(p in text.lower() for p in bad_phrases):
            raise RuntimeError("Ollama response not clean")

        return text

    except Exception as ex:
        print(f"Ollama rewrite skipped: {ex}")
        return facts

def get_statewide_forecast():
    try:
        products = get_json(
            "https://api.weather.gov/products/types/AFD/locations/ABQ"
        )

        latest_id = products["@graph"][0]["id"]
        product = get_json(f"https://api.weather.gov/products/{latest_id}")
        text = product["productText"]

        # Remove the NWS header
        updated = re.search(r"Updated at.*?\n", text, re.DOTALL)
        if updated:
            text = text[updated.end():]

        # Capture wrapped key-message bullet blocks
        bullet_blocks = re.findall(
            r"^\s*-\s+(.*?)(?=\n\s*-\s+|\n&&|\n\.[A-Z]|\Z)",
            text,
            re.MULTILINE | re.DOTALL
        )

        messages = []

        for b in bullet_blocks[:4]:
            b = re.sub(r"\s+", " ", b).strip()
            b = b.replace(" NM", " New Mexico")
            b = b.replace("NM.", "New Mexico.")
            b = b.replace("east central", "east-central")
            messages.append(b)

        if messages:
            raw_forecast = " ".join(messages)
            forecast = rewrite_statewide_with_ollama(raw_forecast)
        else:
            forecast = (
                "Weather conditions will vary across New Mexico today. "
                "See the local forecasts below for additional details."
            )


        return "STATEWIDE FORECAST\n\n" + forecast

    except Exception as ex:
        print(f"Statewide forecast error: {ex}")
        return (
            "STATEWIDE FORECAST\n\n"
            "Weather conditions will vary across New Mexico today."
        )


def get_metro_forecast():
    periods = get_forecast_periods(35.0844, -106.6504)

    lines = ["METRO FORECAST", ""]

    for period in periods[:3]:
        name = period["name"]
        temp_label = "High" if period["isDaytime"] else "Low"
        temp = period["temperature"]
        wind = f"{period['windDirection']} {period['windSpeed']}"
        rain = period.get("probabilityOfPrecipitation", {}).get("value")

        rain_text = ""
        if rain is not None and rain > 0:
            rain_text = f" Chance of precipitation {rain}%."

        lines.append(
            f"{name}: {period['shortForecast']}. "
            f"{temp_label} {temp}°. "
            f"Wind {wind}.{rain_text}"
        )

    lines.append("")
    lines.append("METRO TEMPERATURES")
    lines.append("")
    lines.append(f"{'Area':<10} {'High/Low':>10} {'Current':>10}  Forecast")
    lines.append("-" * 55)

    for area, (lat, lon) in METRO_AREAS.items():
        selected = pick_city_period(get_forecast_periods(lat, lon))
        current = get_current_temp(lat, lon)

        lines.append(
            f"{area:<10}"
            f"{str(selected['temperature']) + '°':>10}"
            f"{str(current) + '°':>10}  "
            f"{selected['shortForecast']}"
        )

    return "\n".join(lines)

def get_sunrise_sunset():
    city = LocationInfo(
        "Albuquerque",
        "New Mexico",
        "America/Denver",
        35.0844,
        -106.6504,
    )

    s = sun(city.observer, date=datetime.now(TZ).date(), tzinfo=TZ)
    sunrise = s["sunrise"].strftime("%I:%M %p").lstrip("0")
    sunset = s["sunset"].strftime("%I:%M %p").lstrip("0")

    return f"Sunrise in Albuquerque is {sunrise}. Sunset is {sunset}."

def get_city_table():
    now = datetime.now(TZ)
    label = "High" if now.hour < 15 else "Low"

    lines = [
        "CITY FORECAST",
        "",
        f"{'City':<14} {'Current':>8} {label:>8}  Forecast",
        "-" * 52,
    ]

    for city, (lat, lon) in LOCATIONS.items():
        p = pick_city_period(get_forecast_periods(lat, lon))
        current = get_current_temp(lat, lon)

        lines.append(
            f"{city:<14} "
            f"{str(current) + '°':>8} "
            f"{str(p.get('temperature')) + '°':>8}  "
            f"{p.get('shortForecast', '')}"
        )

    return "\n".join(lines)

def get_closing():
    return random.choice(CLOSINGS)

def build_weather_email(
    statewide,
    alerts,
    metro,
    city_table,
    sun,
    closing
):
    alert_section = ""

    if alerts:
        alert_section = "WEATHER ALERTS\n\n"

        for alert in alerts:
            alert_section += f"{alert}\n"

        alert_section += "\n"

    return f"""{statewide}

{alert_section}{metro}

{city_table}

SUNRISE / SUNSET

{sun}

{closing}
"""

def build_metro_rows_html():
    rows = ""

    for area, (lat, lon) in METRO_AREAS.items():
        current = get_current_temp(lat, lon)
        p = pick_city_period(get_forecast_periods(lat, lon))

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{area}</td>
            <td style="padding:8px;text-align:center;border-bottom:1px solid #ddd;font-weight:bold;">{p.get('temperature')}°</td>
            <td style="padding:8px;text-align:center;border-bottom:1px solid #ddd;font-weight:bold;">{current}°</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{p.get('shortForecast', '')}</td>
        </tr>
        """

    return rows


def build_city_rows_html():
    rows = ""

    for city, (lat, lon) in LOCATIONS.items():
        current = get_current_temp(lat, lon)
        p = pick_city_period(get_forecast_periods(lat, lon))

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{city}</td>
            <td style="padding:8px;text-align:center;border-bottom:1px solid #ddd;font-weight:bold;">{current}°</td>
            <td style="padding:8px;text-align:center;border-bottom:1px solid #ddd;font-weight:bold;">{p.get('temperature')}°</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{p.get('shortForecast', '')}</td>
        </tr>
        """

    return rows


def build_statewide_weather_data():
    locations = {
        "Albuquerque": (35.0844, -106.6504),
        "Santa Fe": (35.6870, -105.9378),
        "Farmington": (36.7281, -108.2187),
        "Gallup": (35.5281, -108.7426),
        "Las Vegas": (35.5942, -105.2228),
        "Roswell": (33.3943, -104.5230),
        "Taos": (36.4072, -105.5731),
        "Española": (36.0078, -106.0622),
    }

    lines = []

    for name, (lat, lon) in locations.items():
        try:
            point = get_json(
                f"https://api.weather.gov/points/{lat},{lon}"
            )

            props = point["properties"]

            forecast = get_json(props["forecast"])
            periods = forecast["properties"]["periods"]

            if not periods:
                continue

            current = periods[0]

            line = (
                f"{name}: "
                f"{current.get('name', '')}. "
                f"{current.get('detailedForecast', '')} "
                f"Temperature {current.get('temperature', '')}"
                f"{current.get('temperatureUnit', '')}. "
                f"Wind {current.get('windDirection', '')} "
                f"{current.get('windSpeed', '')}."
            )

            pop = (
                current.get("probabilityOfPrecipitation") or {}
            ).get("value")

            if pop is not None:
                line += f" Precipitation chance {pop}%."

            lines.append(line)

        except Exception as e:
            print(f"Statewide forecast data failed for {name}: {e}")

    return "\n".join(lines)


def build_weather_email_html(statewide, alerts, metro, sun, closing):

    statewide_html = statewide.replace("STATEWIDE FORECAST", "").strip()
    closing_html = closing.replace("\n", "<br>")

    metro_html = ""
    current_period = None

    for line in metro.splitlines():
        line = line.strip()

        if not line:
            continue

        if line in ["Today", "Tonight", "Tomorrow", "Tomorrow Night"]:
            current_period = line
            metro_html += f"<h3 style='color:#333;margin-top:16px;'>{line}</h3>"
            continue

        if line.startswith("METRO"):
            continue

        if line.startswith("High:") or line.startswith("Low:"):
            metro_html += f"<p><strong>{line}</strong></p>"
        elif line.startswith("Wind:") or line.startswith("Humidity:") or line.startswith("Chance of precipitation:"):
            label, text = line.split(":", 1)
            metro_html += f"<p><strong>{label}:</strong> {text.strip()}</p>"
        elif not line.startswith("Area") and not line.startswith("-") and not line.startswith("Valley") and not line.startswith("Heights"):
            metro_html += f"<p>{line}</p>"
    alerts_html = ""

    if alerts:
        alert_items = ""

        for alert in alerts:
            alert_items += f"<li style='margin-bottom:6px;'>{alert}</li>"

        alerts_html = f"""
        <div style="
            margin:20px 0;
            padding:15px;
            background:#fff3cd;
            border:1px solid #ffe69c;
        ">
            <h2 style="margin-top:0;">Weather Alerts</h2>
            <ul style="margin-bottom:0;">
                {alert_items}
            </ul>
        </div>
        """
    return f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;color:#222;">
<div style="max-width:760px;margin:0 auto;background:#fff;padding:24px;">

<div style="border-bottom:4px solid #1f5f99;padding-bottom:12px;margin-bottom:22px;">
<h1 style="margin:0;color:#1f5f99;">KANW New Mexico Weather</h1>
<p style="margin-top:6px;color:#666;">
Automated weather briefing for 89.1 KANW FM
</p>
</div>

<h2 style="color:#1f5f99;">Statewide Forecast</h2>

<p>{statewide_html}</p>

{alerts_html}

<h2 style="color:#1f5f99;">Metro Forecast</h2>

{metro_html}

<h3>Metro Temperatures</h3>

<table style="width:100%;border-collapse:collapse;">
<thead>
<tr style="background:#1f5f99;color:white;">
<th>Area</th>
<th>High/Low</th>
<th>Current</th>
<th>Forecast</th>
</tr>
</thead>

<tbody>

{build_metro_rows_html()}

</tbody>

</table>

<h2 style="color:#1f5f99;">City Forecast</h2>

<table style="width:100%;border-collapse:collapse;">
<thead>
<tr style="background:#1f5f99;color:white;">
<th>City</th>
<th>Current</th>
<th>High/Low</th>
<th>Forecast</th>
</tr>
</thead>

<tbody>

{build_city_rows_html()}

</tbody>

</table>

<h2 style="color:#1f5f99;">Sunrise / Sunset</h2>

<p>{sun}</p>

<p style="margin-top:25px;border-top:1px solid #ccc;padding-top:15px;">
{closing_html}
</p>

</div>
</body>
</html>
"""


def send_email(subject, text_body, html_body=None, to_email=None):
    msg = EmailMessage()
    msg["From"] = os.getenv("FROM_EMAIL", "").strip()
    recipient = to_email or os.getenv("TO_EMAIL", "").strip()
    msg["To"] = recipient
    msg["Subject"] = subject

    msg.set_content(text_body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")

    user = os.getenv("FROM_EMAIL", "").strip()
    password = os.getenv("APP_PASSWORD", "").replace(" ", "").strip()

    with smtplib.SMTP(
        os.getenv("SMTP_SERVER"),
        int(os.getenv("SMTP_PORT", "587")),
    ) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def already_triggered(uid):
    if not os.path.exists(STATE_FILE):
        return False

    with open(STATE_FILE, "r") as f:
        return uid in f.read()


def mark_triggered(uid):
    with open(STATE_FILE, "a") as f:
        f.write(uid + "\n")


def should_trigger():
    calendar_url = os.getenv("GOOGLE_CALENDAR_ICS_URL", "").strip()

    if not calendar_url:
        raise RuntimeError(
            "GOOGLE_CALENDAR_ICS_URL is not configured in .env"
        )

    now = datetime.now(TZ)

    r = SESSION.get(calendar_url, timeout=15)
    r.raise_for_status()

    cal = Calendar.from_ical(r.content)

    for component in cal.walk("VEVENT"):
        title = str(component.get("SUMMARY", "")).strip().lower()

        if "nm music" not in title:
            continue

        uid = str(component.get("UID", "")).strip()

        dtstart = component.decoded("DTSTART")
        dtend = component.decoded("DTEND")

        if not isinstance(dtstart, datetime):
            continue

        if not isinstance(dtend, datetime):
            continue

        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=TZ)
        else:
            dtstart = dtstart.astimezone(TZ)

        if dtend.tzinfo is None:
            dtend = dtend.replace(tzinfo=TZ)
        else:
            dtend = dtend.astimezone(TZ)

        duration = dtend - dtstart

        rrule = component.get("RRULE")

        # Recurring event
        if rrule:
            rule_text = rrule.to_ical().decode()

            rule = rrulestr(
                rule_text,
                dtstart=dtstart,
            )

            occurrence = rule.before(now, inc=True)

            if occurrence:
                if occurrence.tzinfo is None:
                    occurrence = occurrence.replace(tzinfo=TZ)
                else:
                    occurrence = occurrence.astimezone(TZ)

                occurrence_end = occurrence + duration

                if not (occurrence <= now < occurrence_end):
                    continue

                hour_key = now.strftime("%Y-%m-%d-%H")
                state_key = f"{uid}|{hour_key}"

                if already_triggered(state_key):
                    continue

                mark_triggered(state_key)

                print(
                    f"Matched recurring Google Calendar event: "
                    f"{title} ({occurrence} - {occurrence_end})"
                )

                return True
    return False

def main():
    parser = argparse.ArgumentParser(
        description="KANW weather email agent"
    )

    parser.add_argument(
        "--email",
        action="store_true",
        help="Send the weather email regardless of Calendar status.",
    )

    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate the weather briefing without sending email.",
    )

    parser.add_argument(
        "--to",
        help=(
            "Override destination email address(es). "
            "Separate multiple addresses with commas."
        ),
    )

    args = parser.parse_args()

    # Preview always generates the report but never sends.
    if args.preview:
        print("Preview mode: generating weather without sending.\n")
        should_send = False

    # --email forces an email without requiring a calendar event.
    elif args.email:
        print("Email mode: sending regardless of Calendar status.\n")
        should_send = True

    # Normal cron execution requires an active NM Music event.
    else:
        should_send = should_trigger()

        if not should_send:
            print("No active NM Music calendar event.")
            return

    statewide = get_statewide_forecast()
    metro = get_metro_forecast()
    city_table = get_city_table()
    sun = get_sunrise_sunset()
    closing = get_closing()
    alerts = get_nm_alerts()

    text_message = build_weather_email(
        statewide,
        alerts,
        metro,
        city_table,
        sun,
        closing,
    )

    html_message = build_weather_email_html(
        statewide,
        alerts,
        metro,
        sun,
        closing,
    )

    if args.preview:
        destination = args.to or os.getenv("TO_EMAIL")

        print(f"Destination email: {destination}\n")
        print("==== WEATHER EMAIL (TEXT) ====\n")
        print(text_message)
        print("\n==============================\n")

        preview_file = (
            Path(__file__).parent
            / "tmp"
            / "weather_preview.html"
        )

        preview_file.parent.mkdir(exist_ok=True)

        with open(
            preview_file,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(html_message)

        print(f"HTML preview written to {preview_file}")
        print("Preview complete. Email not sent.")
        return

    if should_send:
        send_email(
            "KANW NM Music Weather",
            text_message,
            html_message,
            args.to,
        )

        print("Weather email sent.")

if __name__ == "__main__":
    main()