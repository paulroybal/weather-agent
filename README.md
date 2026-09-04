# KANW Weather Agent

KANW Weather Agent is a Python application that generates an automated
New Mexico weather briefing for **KANW 89.1 FM**. It retrieves forecast
and alert information from the National Weather Service, creates a
statewide radio-friendly forecast with a local Ollama model, adds
Albuquerque metro and selected New Mexico city conditions, and sends the
finished briefing by email.

The application is designed to run automatically from `cron`. A weather
briefing is sent when a matching **NM Music** Google Calendar event is
active. An email can also be generated manually from the command line.

## Features

-   Retrieves official forecast data from the National Weather Service
    API
-   Generates a concise statewide New Mexico forecast
-   Uses a local Ollama model to turn forecast data into natural radio
    copy
-   Includes Albuquerque metro forecasts
-   Includes Valley and Heights temperatures
-   Includes current conditions and forecasts for selected New Mexico
    cities
-   Includes active New Mexico weather alerts in a separate section
-   Calculates Albuquerque sunrise and sunset
-   Produces both plain-text and HTML email
-   Uses Google Calendar to determine when an NM Music program is active
-   Supports recurring Google Calendar events
-   Prevents duplicate calendar-triggered emails during the same hour
-   Supports manual email and preview modes
-   Retries temporary HTTP/API failures

## Weather Sources

Weather data is obtained from the **National Weather Service API** at
`api.weather.gov`.

The application uses NWS point forecasts, hourly forecasts, active New
Mexico alerts, and Albuquerque forecast discussion products.

Representative locations are used to help create a statewide picture of
conditions across New Mexico.

## Requirements

-   Python 3
-   Internet access
-   An SMTP email account
-   A Google Calendar secret iCal URL
-   Ollama running locally or on a reachable host

Python packages used by the application include:

-   `requests`
-   `python-dotenv`
-   `icalendar`
-   `python-dateutil`
-   `astral`

Install the project's dependencies with:

``` bash
pip install -r requirements.txt
```

## Ollama

The statewide forecast is rewritten into natural broadcast copy using
Ollama.

By default, the application expects Ollama at:

``` text
http://127.0.0.1:11434
```

and uses:

``` text
llama3.2:3b
```

These values can be changed through environment variables:

``` text
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b
```

Make sure the configured model has already been installed in Ollama.

For example:

``` bash
ollama pull llama3.2:3b
```

## Configuration

Create a `.env` file in the project directory.

Example:

``` text
FROM_EMAIL=your-email@example.com
TO_EMAIL=destination@example.com

SMTP_SERVER=mail.example.com
SMTP_PORT=587
APP_PASSWORD=your-email-password

GOOGLE_CALENDAR_ICS_URL=your-secret-google-calendar-ical-url

OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b
```

**Do not commit `.env` to GitHub.**

The Google Calendar secret iCal URL and email credentials should be
treated as passwords.

A `.gitignore` should include at least:

``` text
.env
.venv/
__pycache__/
tmp/
state.txt
```

## Google Calendar Trigger

During normal operation, the program checks the configured Google
Calendar for an active event containing:

``` text
NM Music
```

The calendar controls whether a weather email should be sent; `cron`
controls how frequently the program runs.

This allows the radio schedule to be changed in Google Calendar without
changing the server's cron configuration.

Recurring calendar events are supported.

The application records calendar-triggered sends in `state.txt` to
prevent duplicate emails during the same hour.

## Command-Line Usage

### Normal operation

``` bash
python weather_agent.py
```

The program checks Google Calendar. If an NM Music event is currently
active, the weather briefing is generated and emailed. If no matching
event is active, the program exits without sending an email.

### Send an email manually

``` bash
python weather_agent.py --email
```

This sends the weather briefing regardless of Google Calendar status.

### Send to a different address

``` bash
python weather_agent.py --email --to someone@example.com
```

### Preview without sending

``` bash
python weather_agent.py --preview
```

Preview mode generates the weather report and writes an HTML preview to:

``` text
tmp/weather_preview.html
```

No email is sent.

## Automated Operation

The application is intended to be run periodically with `cron`.

For example:

``` cron
0 * * * * /path/to/python /path/to/weather-agent/weather_agent.py
```

The exact cron schedule can be adjusted as needed. Because Google
Calendar determines whether an NM Music event is active, cron does not
need to contain the radio program schedule itself.

## Email Layout

The generated email contains:

1.  Statewide Forecast
2.  Weather Alerts, when active
3.  Albuquerque Metro Forecast
4.  Metro Temperatures
5.  New Mexico City Forecasts
6.  Sunrise / Sunset
7.  KANW closing line

The HTML version provides a formatted briefing while the plain-text
version is suitable for quick reference and on-air use.

## Project Structure

A typical installation looks like:

``` text
weather-agent/
├── weather_agent.py
├── requirements.txt
├── README.md
├── .env
├── state.txt
└── tmp/
    └── weather_preview.html
```

The `.env`, `state.txt`, virtual environment, and generated preview
files should normally remain outside version control.

## Security

Never commit any of the following to the repository:

-   SMTP passwords
-   Google Calendar secret iCal URLs
-   `.env`
-   API credentials or authentication tokens

If a secret is accidentally committed, revoke or replace it rather than
simply deleting it from the latest commit.

## About KANW

This project generates weather briefing material for use with **KANW
89.1 FM** and its New Mexico Music programming.

Weather information should always be considered supplemental to official
National Weather Service products, watches, warnings, and advisories.

## License

No license is currently specified for this project.
