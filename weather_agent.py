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


# Forecast offices covering New Mexico. Some EPZ/MAF discussion also
# covers Texas; the radio summary must use only New Mexico information.
AFD_OFFICES = {
    "ABQ": "northern and central New Mexico",
    "EPZ": "southern New Mexico",
    "MAF": "southeastern New Mexico",
}


def get_latest_afd_text(office="ABQ"):
    """Return the latest full Area Forecast Discussion for an NWS office."""
    products = get_json(
        f"https://api.weather.gov/products/types/AFD/locations/{office}"
    )
    graph = products.get("@graph", [])
    if not graph:
        raise RuntimeError(f"No AFD products found for {office}")

    # The API normally lists newest first; prefer the issuance timestamp if
    # available so we don't accidentally choose an older discussion.
    latest = max(graph, key=lambda item: item.get("issuanceTime") or "")
    product = get_json(f"https://api.weather.gov/products/{latest['id']}")
    discussion = product.get("productText", "")
    if not discussion.strip():
        raise RuntimeError(f"Empty AFD product for {office}")
    return discussion


def extract_afd_highlights(text):
    """Select relevant forecast prose, not aviation/fire sections or headers."""
    # NWS AFD section headings vary by office and over time.
    sections = re.split(
        r"(?m)^\s*\.([A-Z][A-Z0-9 /-]*?)\.{2,}\s*",
        text,
    )
    selected = []
    for i in range(1, len(sections), 2):
        name = sections[i].strip().upper()
        body = sections[i + 1].split("\n&&", 1)[0]
        if any(tag in name for tag in ("AVIATION", "FIRE WEATHER", "HYDROLOGY")):
            continue
        if "KEY MESSAGE" in name:
            limit = 800
        elif "SYNOPSIS" in name:
            limit = 650
        elif "SHORT TERM" in name or "DISCUSSION" in name:
            limit = 1100
        elif "LONG TERM" in name:
            limit = 650
        else:
            continue
        prose = clean_afd_text(body)
        if prose:
            selected.append(f"{name}: {prose[:limit]}")

    if not selected:
        # AFDs sometimes use unmarked 'Key Messages:' instead of .SECTIONS...
        key = re.search(
            r"(?ims)^\s*Key Messages?\s*:?\s*(.*?)"
            r"(?=^\s*\.[A-Z][A-Z0-9 /-]*\.{2,}|^\s*&&|\Z)",
            text,
        )
        if key:
            selected.append("KEY MESSAGES: " + clean_afd_text(key.group(1))[:1000])

    if not selected:
        # Last resort: strip the product header/footers and supply a brief
        # excerpt; we don't discard an entire office for formatting changes.
        body = text.split("\n\n", 1)[-1]
        body = re.split(r"(?m)^\s*\.AVIATION", body, maxsplit=1)[0]
        selected.append(clean_afd_text(body)[:1100])

    return "\n".join(selected[:4])


def get_statewide_afd_highlights():
    """Get independent AFDs; one unavailable office won't break the report."""
    reports = []
    for office, region in AFD_OFFICES.items():
        try:
            discussion = get_latest_afd_text(office)
            highlights = extract_afd_highlights(discussion)
            if highlights.strip():
                reports.append(f"{office} ({region}):\n{highlights}")
        except Exception as ex:
            print(f"AFD retrieval failed for {office}: {ex}")
    return "\n\n".join(reports)


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
            "num_predict": 220,
        },
    }

    r = SESSION.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["response"].strip()


def rewrite_statewide_with_ollama(afd_highlights, statewide_data):
    prompt = f"""
You write a statewide NEW MEXICO weather forecast read aloud on KANW radio.
Write ONE concise, conversational paragraph of 65 to 80 words, without a title,
introduction, bullets, or a separate alerts section. Do not exceed 85 words.
Make it easy to read aloud in about 30 to 40 seconds.

Summarize the most important conditions for today and tonight, including
tomorrow only when a significant change is forecast. Include regional
contrasts, precipitation, wind, or hazards only when noteworthy.
Prioritize what a New Mexico listener would need to know. Use short, clear
sentences, avoid repetitive phrasing and do not recite individual city forecasts.

Use ONLY facts supported by the NWS discussions and point forecasts below.
Some EPZ and MAF discussions also refer to Texas: EXCLUDE Texas weather,
including Texas-only hazards. Do not conflate different forecast periods,
misstate highs as lows, invent rainfall amounts, or invent weather systems.
If some offices are unavailable, do not infer their missing conditions.

NWS AREA FORECAST DISCUSSIONS (some sections are excerpts):
{afd_highlights or 'No Area Forecast Discussions available.'}

REPRESENTATIVE NEW MEXICO POINT FORECASTS:
{statewide_data or 'Point forecasts unavailable.'}
"""
    try:
        result = ask_ollama(prompt).strip().strip('"')
        if len(result.split()) < 35 or len(result.split()) > 95:
            raise RuntimeError("Ollama statewide forecast length out of range")
        if any(phrase in result.lower() for phrase in (
            "as an ai", "here is the forecast", "nws area forecast discussions",
            "representative new mexico point forecasts",
        )):
            raise RuntimeError("Ollama response contains instructions or headings")
        return result
    except Exception as ex:
        print(f"Ollama rewrite skipped: {ex}")
        # Provide useful sourced prose instead of a fabricated statewide outlook.
        if afd_highlights:
            first = afd_highlights.split("\n\n", 1)[0]
            first = re.sub(r"^[^\n]*\n", "", first)
            first = re.sub(r"^[A-Z /-]+:\s*", "", first)
            cleaned = clean_afd_text(first)
            sentences = re.split(r"(?<=[.!?])\s+", cleaned)
            selected = []
            for sentence in sentences:
                if len((" ".join(selected + [sentence])).split()) > 80:
                    break
                selected.append(sentence)
            return " ".join(selected) if selected else " ".join(cleaned.split()[:80]).rstrip(",;:") + "."
        return (
            "A statewide narrative is temporarily unavailable. "
            "See the metro and city forecasts below for local conditions."
        )


def get_statewide_forecast():
    try:
        afd_highlights = get_statewide_afd_highlights()
        statewide_data = build_statewide_weather_data()
        forecast = rewrite_statewide_with_ollama(afd_highlights, statewide_data)
        return "STATEWIDE FORECAST\n\n" + forecast
    except Exception as ex:
        print(f"Statewide forecast error: {ex}")
        return (
            "STATEWIDE FORECAST\n\n"
            "A statewide narrative is temporarily unavailable. "
            "See the metro and city forecasts below for local conditions."
        )


def is_afternoon_broadcast(now=None):
    """3 PM local New Mexico time is the broadcast cutoff."""
    return (now or datetime.now(TZ)).hour >= 15


def get_broadcast_periods(lat, lon, now=None):
    """Return [(Today, Tonight)] or [(Tonight, Tomorrow)] using NWS dates."""
    now = now or datetime.now(TZ)
    today = now.date()
    periods = get_forecast_periods(lat, lon)

    def find_period(day, daytime):
        for period in periods:
            start = parse_date(period["startTime"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=TZ)
            start = start.astimezone(TZ)
            if start.date() == day and period.get("isDaytime") == daytime:
                return period
        return None

    from datetime import timedelta
    tonight = find_period(today, False)
    if is_afternoon_broadcast(now):
        return [("Tonight", tonight),
                ("Tomorrow", find_period(today + timedelta(days=1), True))]
    return [("Today", find_period(today, True)), ("Tonight", tonight)]


def forecast_snapshot(now=None):
    """Keep console and email consistent, and avoid redundant API calls."""
    now = now or datetime.now(TZ)
    metro = get_broadcast_periods(35.0844, -106.6504, now)
    city_forecasts = []
    current_temps = []
    for city, (lat, lon) in LOCATIONS.items():
        try:
            selected = get_broadcast_periods(lat, lon, now)[0][1]
        except Exception as ex:
            print(f"Forecast retrieval error for {city}: {ex}")
            selected = None
        city_forecasts.append((city, selected))
        try:
            # NWS hourly forecast is an estimate, not a station observation.
            temperature = get_current_temp(lat, lon)
        except Exception as ex:
            print(f"Current hourly temperature error for {city}: {ex}")
            temperature = None
        current_temps.append((city, temperature))
    return metro, city_forecasts, current_temps


def temp_display(value):
    return f"{value}°" if value is not None else "N/A"


def get_metro_forecast(metro_periods=None):
    if metro_periods is None:
        metro_periods = get_broadcast_periods(35.0844, -106.6504)
    lines = ["ALBUQUERQUE METRO FORECAST", ""]
    for name, period in metro_periods:
        if not period:
            lines.append(f"{name}: Forecast unavailable.")
            continue
        kind = "High" if period.get("isDaytime") else "Low"
        condition = (period.get("shortForecast") or "Conditions unavailable").rstrip(".")
        lines.append(f"{name}: {kind} {temp_display(period.get('temperature'))}, {condition}.")
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

def get_city_table(city_forecasts=None, now=None):
    now = now or datetime.now(TZ)
    if city_forecasts is None:
        _, city_forecasts, _ = forecast_snapshot(now)
    daytime = not is_afternoon_broadcast(now)
    label = "High" if daytime else "Low"
    lines = [f"CITY {label.upper()} TEMPERATURES", "",
             f"{'City':<16} {label:>6}  Conditions", "-" * 55]
    for city, period in city_forecasts:
        value = temp_display(period.get('temperature')) if period else "N/A"
        conditions = (period.get('shortForecast') or 'Unavailable') if period else 'Forecast unavailable'
        lines.append(f"{city:<16} {value:>6}  {conditions}")
    return "\n".join(lines)


def get_current_temperatures(current_temps=None):
    if current_temps is None:
        _, _, current_temps = forecast_snapshot()
    lines = ["CURRENT TEMPERATURES", "",
             f"{'City':<16} {'Current':>8}", "-" * 28]
    for city, temperature in current_temps:
        lines.append(f"{city:<16} {temp_display(temperature):>8}")
    return "\n".join(lines)


def get_closing():
    return random.choice(CLOSINGS)

def get_report_datestamp():
    """Timestamp in New Mexico local time, at report creation."""
    now = datetime.now(TZ)
    return f"{now.strftime('%A, %B')} {now.day}, {now.year} at {now.strftime('%I:%M %p').lstrip('0')} {now.strftime('%Z')}"


def build_weather_email(statewide, alerts, metro, city_table,
                        current_temps, sun, closing, datestamp=None):
    datestamp = datestamp or get_report_datestamp()
    alert_section = ""
    if alerts:
        alert_section = "WEATHER ALERTS\n\n" + "\n".join(alerts) + "\n\n"
    return f"""KANW NEW MEXICO WEATHER
Issued: {datestamp}

{statewide}

{alert_section}{metro}

{city_table}

{current_temps}

SUNRISE / SUNSET

{sun}

{closing}
"""


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


def build_weather_email_html(statewide, alerts, metro, city_forecasts,
                             current_temps, sun, closing, datestamp=None,
                             afternoon=None):
    datestamp = datestamp or get_report_datestamp()
    if afternoon is None:
        afternoon = is_afternoon_broadcast()

    def safe(value):
        return html.escape(str(value)).replace("°", "&#176;")

    statewide_text = statewide.replace("STATEWIDE FORECAST", "", 1).strip()
    metro_text = metro.replace("ALBUQUERQUE METRO FORECAST", "", 1).strip()
    metro_html = "".join(
        f"<p style='margin:8px 0'>{safe(line)}</p>"
        for line in metro_text.splitlines() if line.strip()
    )
    label = "Low" if afternoon else "High"
    forecast_rows = ""
    for city, period in city_forecasts:
        temperature = temp_display(period.get('temperature')) if period else "N/A"
        condition = (period.get('shortForecast') or 'Unavailable') if period else 'Forecast unavailable'
        forecast_rows += ("<tr>"
                          f"<td>{safe(city)}</td>"
                          f"<td class='numeric'>{safe(temperature)}</td>"
                          f"<td>{safe(condition)}</td></tr>")

    current_rows = "".join(
        f"<tr><td>{safe(city)}</td><td class='numeric'>{safe(temp_display(value))}</td></tr>"
        for city, value in current_temps
    )
    alerts_html = ""
    if alerts:
        items = "".join(f"<li>{safe(alert)}</li>" for alert in alerts)
        alerts_html = ("<section class='alerts'><h2>Weather Alerts</h2>"
                       f"<ul>{items}</ul></section>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KANW New Mexico Weather</title>
<style>
body {{font-family:Arial,Helvetica,sans-serif;color:#222;background:#f4f4f4;margin:0;padding:16px;}}
.container {{max-width:760px;margin:auto;background:white;padding:24px;}}
h1,h2 {{color:#1f5f99;}}
h1 {{margin-bottom:5px;}}
h2 {{margin-top:24px;}}
.datestamp {{font-size:14px;color:#666;font-weight:normal;margin:5px 0 22px;}}
table {{width:100%;border-collapse:collapse;margin-bottom:24px;}}
th {{background:#1f5f99;color:white;padding:9px;text-align:left;}}
td {{padding:9px;border-bottom:1px solid #ddd;}}
.numeric {{text-align:center;white-space:nowrap;}}
.alerts {{background:#fff3cd;border:1px solid #ffe69c;padding:8px 14px;}}
.footer {{margin-top:25px;border-top:1px solid #ccc;padding-top:15px;}}
</style>
</head>
<body>
<div class="container">
<h1>KANW New Mexico Weather</h1>
<p class="datestamp">Issued: {safe(datestamp)}</p>
<h2>Statewide Forecast</h2>
<p>{safe(statewide_text)}</p>
{alerts_html}
<h2>Albuquerque Metro Forecast</h2>
{metro_html}
<h2>City {label} Temperatures</h2>
<table><thead><tr><th>City</th><th>{label}</th><th>Conditions</th></tr></thead>
<tbody>{forecast_rows}</tbody></table>
<h2>Current Temperatures</h2>
<table><thead><tr><th>City</th><th>Current</th></tr></thead>
<tbody>{current_rows}</tbody></table>
<h2>Sunrise / Sunset</h2>
<p>{safe(sun)}</p>
<p class="footer">{safe(closing)}</p>
</div></body></html>"""


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

    report_now = datetime.now(TZ)
    statewide = get_statewide_forecast()
    metro_periods, city_forecasts, current_values = forecast_snapshot(report_now)
    metro = get_metro_forecast(metro_periods)
    city_table = get_city_table(city_forecasts, report_now)
    current_temps = get_current_temperatures(current_values)
    sun = get_sunrise_sunset()
    closing = get_closing()
    alerts = get_nm_alerts()
    datestamp = get_report_datestamp()

    text_message = build_weather_email(
        statewide, alerts, metro, city_table, current_temps,
        sun, closing, datestamp,
    )

    html_message = build_weather_email_html(
        statewide, alerts, metro, city_forecasts, current_values,
        sun, closing, datestamp, is_afternoon_broadcast(report_now),
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