#!/usr/bin/python
# -*- coding:utf-8 -*-
import sys
import os

BASE = os.path.dirname(os.path.realpath(__file__))
picdir = os.path.join(BASE, 'pic')
libdir = os.path.join(BASE, 'lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

import logging
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd2in13_V4

logging.basicConfig(level=logging.INFO)

# --- Configuration ---------------------------------------------------------
# GitHub username whose contribution graph will be rendered
LOGIN = "torvalds"

# Time window to query from GitHub (in days)
# 90 days ≈ last 3 months, keeps the grid readable on a 2.13" display
DAYS = 126

# How many week-columns from that window are actually drawn
# 13–14 weeks ~= 90 days
WEEKS_TO_SHOW = 18

# How often (in hours) the device checks GitHub for changes
# The display is only re-rendered if the total contribution count changes
REFRESH_HOURS = 6

# Size of each cell in the heatmap (pixels)
# Increase for chunkier blocks, decrease to fit more weeks
CELL = 10

# Gap between cells (pixels)
GAP = 2
# --------------------------------------------------------------------------

GQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          firstDay
          contributionDays {
            date
            contributionCount
            weekday
          }
        }
      }
    }
  }
}
"""

def gql_contrib_calendar(login: str, days: int = 90):
    """
    Fetch the GitHub contribution calendar for `login`
    over the last `days` days using the GraphQL API.

    Requires GITHUB_TOKEN to be present in the environment.
    Returns the `contributionCalendar` structure.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN environment variable.")

    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)

    payload = {
        "query": QUERY,
        "variables": {
            "login": login,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    r = requests.post(GQL_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()

    if "errors" in data:
        raise RuntimeError(str(data["errors"]))

    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]

def level_from_count(c: int) -> int:
    """
    Map a raw contribution count to a visual intensity level (0..4).

    The thresholds are tuned for short windows (e.g. 90 days) so that
    the grid keeps contrast even for modest activity.
    """
    if c <= 0: return 0
    if c <= 1: return 1
    if c <= 3: return 2
    if c <= 7: return 3
    return 4

def fill_cell(draw, x, y, s, lvl):
    """
    Draw a single heatmap cell at (x, y) with size `s`.

    Since the panel is 1-bit (black/white), intensity is simulated
    using simple dithering patterns for levels 1–3.
    """
    draw.rectangle((x, y, x+s-1, y+s-1), outline=0, fill=255)

    if lvl == 0:
        return
    if lvl == 4:
        draw.rectangle((x+1, y+1, x+s-2, y+s-2), outline=0, fill=0)
        return

    if lvl == 1:
        step, off = 3, 0
    elif lvl == 2:
        step, off = 2, 1
    else:
        step, off = 2, 0

    for yy in range(y+1, y+s-1):
        for xx in range(x+1, x+s-1):
            if ((xx + yy + off) % step) == 0:
                draw.point((xx, yy), fill=0)

def textbbox_wh(draw, text, font):
    try:
        b = draw.textbbox((0, 0), text, font=font)
        return (b[2] - b[0], b[3] - b[1])
    except Exception:
        return draw.textsize(text, font=font)

def render_calendar(epd, cal, login: str, days: int, weeks_to_show: int, cell: int, gap: int):
    """
    Render the contribution heatmap onto the E-Ink display.

    - Draws a header with username and total contributions.
    - Renders the last `weeks_to_show` weeks as a 7-row grid.
    - Uses `cell` and `gap` to control visual density.
    """
    W, H = epd.height, epd.width
    img = Image.new('1', (W, H), 255)
    draw = ImageDraw.Draw(img)

    font_title = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 14)
    font_small = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 12)

    total = cal.get("totalContributions", 0)
    title = f"{login}"
    subtitle = f"{total} contrib / {days}d"

    draw.text((4, 2), title, font=font_title, fill=0)
    sw, _ = textbbox_wh(draw, subtitle, font_small)
    draw.text((W - sw - 4, 4), subtitle, font=font_small, fill=0)
    draw.line((4, 20, W - 4, 20), fill=0)

    weeks = cal["weeks"][-weeks_to_show:]
    cw = cell + gap
    left = 6
    top = 26

    # Day labels (Sunday, Monday, Wednesday, Friday)
    labels = [("Sun",0),("Mon",1),("Tue",2),("Wed",3),("Thu",4),("Fri",5),("Sat",6)]
    for ch, wd in labels:
        draw.text((left + len(weeks)*cw + 4, top + wd*cw - 2), ch, font=font_small, fill=0)

    for wx, w in enumerate(weeks):
        for d in w["contributionDays"]:
            wd = d["weekday"]
            c = d["contributionCount"]
            lvl = level_from_count(c)

            x = left + wx * cw
            y = top + wd * cw
            fill_cell(draw, x, y, cell, lvl)

    epd.display(epd.getbuffer(img))

def update_display(epd, cal):
    """
    Render calendar data on the e-paper display.
    """
    render_calendar(epd, cal, LOGIN, DAYS, WEEKS_TO_SHOW, CELL, GAP)
    return cal.get("totalContributions", None)

try:
    logging.info("epd2in13_V4 GitHub Contributions")

    epd = epd2in13_V4.EPD()
    epd.init()
    epd.Clear(0xFF)

    cal = gql_contrib_calendar(LOGIN, days=DAYS)
    last_total = update_display(epd, cal)

    while True:
        time.sleep(REFRESH_HOURS * 3600)

        # Re-fetch but only update if the total changed
        cal = gql_contrib_calendar(LOGIN, days=DAYS)
        total = cal.get("totalContributions", None)

        if total != last_total:
            # Full refresh
            last_total = update_display(epd, cal)

except IOError as e:
    logging.info(e)
    logging.debug(traceback.format_exc())

except KeyboardInterrupt:
    logging.info("ctrl + c:")
    epd2in13_V4.epdconfig.module_exit(cleanup=True)
    exit()
