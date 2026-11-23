"""Team board bot. Reads the Slack List "Team board (TEST)" and posts to #team-board.

Usage:  TEAM_BOARD_TOKEN=<bot token> python team_board_bot.py <job> [--dry]
Jobs:
  link     post the board link in the channel
  sync     Done tracking + repeating tasks (no message)
  morning  sync, then the deadline post (nothing is posted on a quiet day)
  monday   sync, start the new week (roll tasks forward, count carried over), then the
           Monday post with the week's numbers and any task carried 3+ weeks
--today=YYYY-MM-DD acts as if it were that day (for testing).
  friday   sync, then the short "Week in numbers" post (counts only, no per-person list)
  auto     what the schedule runs every 15 minutes: sync, then whichever post is due now
           (Mon 9:00 new week, Tue-Fri 9:00 deadlines, Fri 16:00 week in numbers, Matt's time).
           Each post goes out once; posted.json next to this file remembers which went out.
           --now=YYYY-MM-DDTHH:MM acts as if it were that moment (for testing).
  export   write team-timeline-data.json for Matt's timeline page (reads the board, changes nothing)
--dry prints the message instead of posting (sync still writes to the board unless --dry).
The token is never stored in this file.
"""
import json, os, sys, calendar, urllib.request
from datetime import datetime, timedelta, timezone, date

TOKEN = os.environ["TEAM_BOARD_TOKEN"]
LIST_ID = "F0CH74LRCTY"
CHANNEL = "C0CH3MMN469"
TEAM = "T071T4U984F"
BOARD_URL = f"https://standupeightx.slack.com/lists/{TEAM}/{LIST_ID}"

# Column IDs of the board (read from the board on 12 Nov 2026).
COL = {
    "task": "Col0CHB0ELRB3", "product": "Col0CHCQ8K3KN", "status": "Col0CGTMXUBL7",
    "type": "Col0CJ3EMFPRN", "urgent": "Col0CH8TJKG8J", "waiting_on": "Col0CH2S96WRH",
    "watchers": "Col0CH74M1686", "details": "Col0CH3QE4VLM", "repeats": "Col0CHCQ8N12Q",
    "done_on": "Col0CGTMY1LF9", "completed": "Col00", "owner": "Col01", "due": "Col02",
    "when": "Col0CHCUF0HM2", "carried": "Col0CJ3JTSE2U", "rolled": "Col0CH311HLHH",
    "reported": "Col0CHD395FRS", "created_by": "Col0CH96K4BMY", "week_of": "Col0CJ42KSM7S",
    "week": "Col0CH80Z445C", "hours": "Col0CJDN597AR",
}
# The "Week" column holds readable headings like "9 - 13 Nov 2026" for the Timeline view.
# Slack does not let a bot add options to a column, so every week was created in advance
# (12 Nov 2026), one option per Monday from FIRST_WEEK to LAST_WEEK, value "wYYYYMMDD",
# plus "later" for the backlog. Before LAST_WEEK, add more weeks (newest first) the same way.
FIRST_WEEK = date(2026, 10, 26)
LAST_WEEK = date(2028, 9, 4)
STUCK_AFTER = 3  # weeks carried over before the Monday post lists a task as stuck
PRODUCT = {"ai_sponsor": "AI Sponsor", "qa_guru": "QA Guru", "drm": "DRM", "upwork_os": "Upwork OS",
           "rebuild": "Rebuild", "rekindle": "Rekindle", "founders": "Founders", "conscious_labs": "Conscious Labs",
           "operations": "Operations"}


def call(method, body):
    req = urllib.request.Request("https://slack.com/api/" + method, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json; charset=utf-8"})
    r = json.load(urllib.request.urlopen(req, timeout=30))
    if not r.get("ok"):
        sys.exit(f"{method} failed: {r.get('error')} {r.get('response_metadata', '')}")
    return r


TODAY_OVERRIDE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--today=")), None)
NOW_OVERRIDE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--now=")), None)
CANCUN = timezone(timedelta(hours=-5))  # Matt's time zone: UTC-5 all year, no daylight saving
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted.json")


def now_local():
    if NOW_OVERRIDE:
        return datetime.fromisoformat(NOW_OVERRIDE)
    return datetime.now(CANCUN).replace(tzinfo=None)


def today_local():
    if TODAY_OVERRIDE:  # for testing, e.g. --today=2026-11-16 to act as if it were that Monday
        return date.fromisoformat(TODAY_OVERRIDE)
    if NOW_OVERRIDE:
        return now_local().date()
    # Matt's time zone (his Slack profile, checked 12 Nov 2026). Cancun is UTC-5 all year, no daylight saving.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Cancun")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def rows():
    out, cursor = [], None
    while True:
        body = {"list_id": LIST_ID, "limit": 100}
        if cursor:
            body["cursor"] = cursor
        r = call("slackLists.items.list", body)
        for it in r.get("items", []):
            f = {x.get("key"): x for x in it.get("fields", [])}
            g = lambda k, t: (f.get(k) or {}).get(t)
            d = lambda k: (g(k, "date") or [None])[0]
            rt = lambda k: (f.get(k) or {}).get("rich_text") or (
                json.loads(f[k]["value"]) if k in f and isinstance(f[k].get("value"), str) and f[k]["value"].startswith("[") else None)
            out.append({
                "id": it["id"],
                "task": g("task", "text") or "",
                "task_rt": rt("task"),
                "details_rt": rt("details"),
                "status": (g("status", "select") or [""])[0],
                "product": (g("product", "select") or [""])[0],
                "type": (g("type", "select") or [""])[0],
                "repeats": (g("repeats", "select") or [""])[0],
                "owner": g("todo_assignee", "user") or [],
                "watchers": g("watchers", "user") or [],
                "waiting": g("waiting_on", "user") or [],
                "urgent": bool(g("urgent", "checkbox")),
                "completed": bool(g("todo_completed", "checkbox")),
                "due": date.fromisoformat(d("todo_due_date")) if d("todo_due_date") else None,
                "done_on": date.fromisoformat(d("done_on")) if d("done_on") else None,
                "when": (g("when", "select") or [""])[0],
                "carried": int((g("carried_over", "number") or [0])[0] or 0),
                "rolled": date.fromisoformat(d("rolled_over_on_(bot)")) if d("rolled_over_on_(bot)") else None,
                "reported": date.fromisoformat(d("in_friday_post_(bot)")) if d("in_friday_post_(bot)") else None,
                "week_of": date.fromisoformat(d("week_of")) if d("week_of") else None,
                "week": (g("week", "select") or [""])[0],
                "hours": (g("hours", "number") or [None])[0],
                "created": datetime.fromtimestamp(int(it.get("date_created") or 0), timezone.utc),
            })
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return out


def update(row_id, **cells):
    payload = []
    for k, v in cells.items():
        c = {"row_id": row_id, "column_id": COL[k]}
        c.update(v)
        payload.append(c)
    call("slackLists.items.update", {"list_id": LIST_ID, "cells": payload})


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def next_due(base, repeats):
    return {"weekly": base + timedelta(days=7), "biweekly": base + timedelta(days=14),
            "monthly": add_months(base, 1)}.get(repeats)


def week_end(d):
    return d + timedelta(days=6 - d.weekday())  # the Sunday of d's week


def when_for(due, t):
    """Which week a task belongs in, from its due date. No due date = this week."""
    if not due or due <= week_end(t):
        return "this_week"
    if due <= week_end(t) + timedelta(days=7):
        return "next_week"
    return "later"


def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_of_for(done, done_on, when, t):
    """The Monday of the week a task belongs to, for the Timeline view.
    Done tasks stay in the week they were finished; open ones follow When; Later has no week."""
    if done:
        return monday_of(done_on or t)
    return {"this_week": monday_of(t), "next_week": monday_of(t) + timedelta(days=7)}.get(when)


def week_label_value(week_of):
    """The Week option for a Monday: 'wYYYYMMDD', 'later' for no week, None if outside the options."""
    if week_of is None:
        return "later"
    if FIRST_WEEK <= week_of <= LAST_WEEK:
        return "w" + week_of.strftime("%Y%m%d")
    return None


def sync(dry=False):
    """Keep Status and Completed in step, stamp Done on, fill an empty When and Week of, roll repeating tasks forward."""
    t = today_local()
    log = []
    for r in rows():
        if not r["task"] and not r["owner"]:
            continue  # empty row someone is still filling in
        done = r["status"] == "done"
        # Tasks from the form or "Add to list" arrive with no Status: start them at To do.
        if not r["status"] and not r["completed"]:
            log.append(f"Status empty -> To do: {r['task']}")
            if not dry:
                update(r["id"], status={"select": ["to_do"]})
            r["status"] = "to_do"
        # New task with no week picked: put it in a week from its due date (no date = this week).
        if not done and not r["when"]:
            w = when_for(r["due"], t)
            log.append(f"When empty -> {w}: {r['task']}")
            if not dry:
                update(r["id"], when={"select": [w]})
            r["when"] = w
        # Someone ticked Completed but left Status alone: treat it as Done.
        if r["completed"] and not done and not r["done_on"]:
            log.append(f"ticked -> Done: {r['task']}")
            if not dry:
                update(r["id"], status={"select": ["done"]}, done_on={"date": [t.isoformat()]})
            done, r["done_on"] = True, t
        # Status moved back from Done: untick Completed and clear the date.
        elif r["completed"] and not done and r["done_on"]:
            log.append(f"reopened: {r['task']}")
            if not dry:
                update(r["id"], completed={"checkbox": False}, done_on={"date": []})
            continue
        # Status set to Done: tick Completed and stamp the date.
        if done and (not r["completed"] or not r["done_on"]):
            log.append(f"Done -> stamped {t}: {r['task']}")
            if not dry:
                cells = {"completed": {"checkbox": True}}
                if not r["done_on"]:
                    cells["done_on"] = {"date": [t.isoformat()]}
                update(r["id"], **cells)
            r["done_on"] = r["done_on"] or t
        # A finished repeating task: add the next copy, and move the repeat onto it.
        if done and r["repeats"]:
            nd = next_due(r["due"] or r["done_on"] or t, r["repeats"])
            while nd and nd <= t:  # never create a copy that is already overdue
                nd = next_due(nd, r["repeats"])
            log.append(f"repeat -> new copy due {nd}: {r['task']}")
            if not dry:
                fields = [
                    {"column_id": COL["task"], "rich_text": r["task_rt"] or [{"type": "rich_text", "elements": [
                        {"type": "rich_text_section", "elements": [{"type": "text", "text": r["task"]}]}]}]},
                    {"column_id": COL["status"], "select": ["to_do"]},
                    {"column_id": COL["repeats"], "select": [r["repeats"]]},
                    {"column_id": COL["due"], "date": [nd.isoformat()]},
                    {"column_id": COL["when"], "select": [when_for(nd, t)]},
                ]
                wk = week_of_for(False, None, when_for(nd, t), t)
                if wk:
                    fields.append({"column_id": COL["week_of"], "date": [wk.isoformat()]})
                wv = week_label_value(wk)
                if wv:
                    fields.append({"column_id": COL["week"], "select": [wv]})
                for key, val in (("product", r["product"]), ("type", r["type"])):
                    if val:
                        fields.append({"column_id": COL[key], "select": [val]})
                for key, val in (("owner", r["owner"]), ("watchers", r["watchers"])):
                    if val:
                        fields.append({"column_id": COL[key], "user": val})
                if r["urgent"]:
                    fields.append({"column_id": COL["urgent"], "checkbox": True})
                if r["details_rt"]:
                    fields.append({"column_id": COL["details"], "rich_text": r["details_rt"]})
                call("slackLists.items.create", {"list_id": LIST_ID, "initial_fields": fields})
                update(r["id"], repeats={"select": []})
        # Week of: keep it matching, so the Timeline view puts every task under the right week.
        want = week_of_for(done, r["done_on"], r["when"], t)
        if want != r["week_of"]:
            log.append(f"Week of -> {want or 'none (Later)'}: {r['task']}")
            if not dry:
                update(r["id"], week_of={"date": [want.isoformat()] if want else []})
        # Week: the readable heading ("9 - 13 Nov 2026") the Timeline view groups by.
        wv = week_label_value(want)
        if wv is None:
            log.append(f"Week {want} is outside the prepared weeks, skipped: {r['task']}")
        elif wv != r["week"]:
            log.append(f"Week heading -> {wv}: {r['task']}")
            if not dry:
                update(r["id"], week={"select": [wv]})
    print("\n".join(log) or "sync: nothing to change")


def who(users):
    return " ".join(f"<@{u}>" for u in users)


def line(r, extra=""):
    link = f"<{BOARD_URL}?record_id={r['id']}|{r['task']}>"
    prod = PRODUCT.get(r["product"], "")
    flag = ":zap: " if r["urgent"] else ""
    return f"• {flag}{who(r['owner']) or 'No owner yet'}  {link}" + (f"  ·  {prod}" if prod else "") + extra


def day(d):
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def post(text, dry):
    if dry:
        print(text)
    else:
        call("chat.postMessage", {"channel": CHANNEL, "text": text, "unfurl_links": False, "unfurl_media": False})
        print("posted")


def roll(dry):
    """Monday: start the new week. Unfinished This week tasks stay and count +1 carried over,
    Next week tasks move to This week, Later tasks due this week move to This week.
    Each row is rolled at most once per week (the bot writes the date it rolled it)."""
    t = today_local()
    monday = t - timedelta(days=t.weekday())
    log = []
    for r in rows():
        if not r["task"] or r["status"] == "done":
            continue
        if r["rolled"] and r["rolled"] >= monday:
            continue  # already rolled this week
        cells = {"rolled": {"date": [t.isoformat()]}}
        if r["when"] == "this_week":
            cells["carried"] = {"number": [r["carried"] + 1]}
            log.append(f"carried over ({r['carried'] + 1}): {r['task']}")
        elif r["when"] == "next_week":
            cells["when"] = {"select": ["this_week"]}
            log.append(f"next week -> this week: {r['task']}")
        elif r["when"] == "later" and r["due"] and r["due"] <= week_end(t):
            cells["when"] = {"select": ["this_week"]}
            log.append(f"later -> this week (due {r['due']}): {r['task']}")
        else:
            continue
        if not dry:
            update(r["id"], **cells)
    print("\n".join(log) or "roll: nothing to move")


def morning(dry, monday=False):
    t = today_local()
    open_rows = [r for r in rows() if r["status"] != "done" and r["task"]]
    blocked = [r for r in open_rows if r["status"] == "blocked"]
    rest = [r for r in open_rows if r["status"] != "blocked" and r["due"]]
    overdue = sorted([r for r in rest if r["due"] < t], key=lambda r: r["due"])
    today = [r for r in rest if r["due"] == t]
    tomorrow = [r for r in rest if r["due"] == t + timedelta(days=1)]
    # The full week's list only goes in the Monday post, so the daily post stays short.
    later_this_week = sorted([r for r in rest if t + timedelta(days=1) < r["due"] <= week_end(t)],
                             key=lambda r: r["due"]) if monday else []
    # Blocked tasks are listed once, under Blocked, never again under stuck.
    long_carried = [r for r in open_rows if r["status"] != "blocked" and r["carried"] >= STUCK_AFTER] if monday else []
    stuck = sorted([r for r in long_carried if r["due"]], key=lambda r: -r["carried"])
    no_date = sorted([r for r in long_carried if not r["due"]], key=lambda r: -r["carried"])
    # Tasks added from a message or the form with nobody on them would otherwise sit unseen.
    no_owner = [r for r in open_rows if not r["owner"]]
    # Monday only: who still has tasks with no hours estimate (Mariam, 23 Nov). A count per person,
    # not the full list, so the post stays short as the team grows.
    no_estimate = {}
    if monday:
        for r in open_rows:
            if not r["hours"]:
                for u in (r["owner"] or ["nobody"]):
                    no_estimate[u] = no_estimate.get(u, 0) + 1
    if not (blocked or overdue or today or tomorrow or later_this_week or stuck or no_date or no_owner or no_estimate):
        print("nothing due, no post")
        return
    parts = [f"*{'New week' if monday else 'Deadlines'}, {day(t)}*"]
    if monday:
        this_week = [r for r in open_rows if r["when"] == "this_week"]
        carried = [r for r in this_week if r["carried"] > 0]
        parts.append(f"{len(this_week)} open tasks this week, {len(carried)} carried over from last week.")
    if overdue:
        parts.append("\n:red_circle: *Overdue*\n" + "\n".join(line(r, f"  (was due {day(r['due'])})") for r in overdue))
    if blocked:
        def b(r):
            w = f"  ·  waiting on {who(r['waiting'])}" if r["waiting"] else "  ·  waiting on: not filled in"
            c = f"  ·  carried {r['carried']} week{'s' if r['carried'] != 1 else ''}" if r["carried"] else ""
            return line(r, w + c)
        parts.append("\n:no_entry: *Blocked*\n" + "\n".join(b(r) for r in blocked))
    if today:
        parts.append("\n:large_yellow_circle: *Due today*\n" + "\n".join(line(r) for r in today))
    if tomorrow:
        parts.append("\n:white_circle: *Due tomorrow*\n" + "\n".join(line(r) for r in tomorrow))
    if later_this_week:
        parts.append("\n:spiral_calendar_pad: *Due later this week*\n" + "\n".join(
            line(r, f"  ({day(r['due'])})") for r in later_this_week))
    if stuck:
        parts.append(f"\n:hourglass: *Carried over {STUCK_AFTER}+ weeks*\n" + "\n".join(
            line(r, f"  ({r['carried']} weeks)") for r in stuck))
    if no_date:
        parts.append(f"\n:thinking_face: *No due date, carried {STUCK_AFTER}+ weeks:* split it into smaller tasks, give it a date, or move it to Later?\n"
                     + "\n".join(line(r, f"  ({r['carried']} weeks)") for r in no_date))
    if no_owner:
        parts.append("\n:bust_in_silhouette: *No owner yet*\n" + "\n".join(
            f"• <{BOARD_URL}?record_id={r['id']}|{r['task']}>" + (f"  ·  {PRODUCT[r['product']]}" if r["product"] in PRODUCT else "")
            for r in no_owner))
    if no_estimate:
        parts.append("\n:stopwatch: *No hours estimate yet:* please add one in the Hours column\n" + "\n".join(
            f"• {'No owner' if u == 'nobody' else who([u])}: {n} task{'s' if n != 1 else ''}"
            for u, n in sorted(no_estimate.items(), key=lambda x: -x[1])))
    if monday and t >= LAST_WEEK - timedelta(weeks=8):
        parts.append(f"\n:warning: The board's prepared weeks run out after the week of {day(LAST_WEEK)}. "
                     "Ask Claude to add the next batch of weeks to the Week column.")
    parts.append(f"\n<{BOARD_URL}|Open the Team board>")
    post("\n".join(parts), dry)


def friday(dry):
    """Friday: a short summary in numbers. No list of who did what (Mariam, 23 Nov: too much as the team grows)."""
    t = today_local()
    all_rows = [r for r in rows() if r["task"]]
    # Every Done task not counted yet, so a task finished after last Friday's post is never lost.
    done = [r for r in all_rows if r["status"] == "done" and r["done_on"] and not r["reported"] and r["done_on"] <= t]
    open_rows = [r for r in all_rows if r["status"] != "done"]
    doing = sum(1 for r in open_rows if r["status"] == "doing")
    blocked = sum(1 for r in open_rows if r["status"] == "blocked")
    to_do = len(open_rows) - doing - blocked
    monday = t - timedelta(days=t.weekday())
    added = sum(1 for r in all_rows if r["created"].astimezone(CANCUN).date() >= monday)
    text = (f"*Week in numbers, {day(t)}*\n"
            f":white_check_mark: {len(done)} done  ·  :hammer_and_wrench: {doing} in progress  ·  "
            f":no_entry: {blocked} blocked  ·  :spiral_note_pad: {to_do} to do\n"
            f"{added} new task{'s' if added != 1 else ''} added this week.  <{BOARD_URL}|Open the Team board>")
    post(text, dry)
    if not dry and done:  # mark them counted only after the post went out
        for i in range(0, len(done), 100):
            call("slackLists.items.update", {"list_id": LIST_ID, "cells": [
                {"row_id": r["id"], "column_id": COL["reported"], "date": [t.isoformat()]} for r in done[i:i + 100]]})


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, dry):
    if dry:
        return
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def auto(dry):
    """Run by the schedule every 15 minutes. GitHub starts scheduled runs hours late or skips them,
    so instead of one run per post, every run checks whether a post is due and not yet sent.
    Posts (Matt's time): Mon 9:00-17:00 new week, Tue-Fri 9:00-17:00 deadlines (skipped if the
    window passes, a deadline post in the evening is no use), Fri 16:00 to end of Sat week in numbers."""
    now = now_local()
    t, wd = now.date(), now.weekday()
    state = load_state()
    sync(dry)
    if wd == 0 and now.hour >= 9 and state.get("rolled") != t.isoformat():
        # The week rolls forward even if the post window is missed. roll() moves each row once a week.
        roll(dry)
        sync(dry)  # again, so Week of follows the tasks the roll just moved into this week
        state["rolled"] = t.isoformat()
        save_state(state, dry)
    if 9 <= now.hour < 17 and wd <= 4:
        key = "monday" if wd == 0 else "morning"
        if state.get(key) != t.isoformat():
            morning(dry, monday=(wd == 0))
            state[key] = t.isoformat()
            save_state(state, dry)
    if (wd == 4 and now.hour >= 16) or wd == 5:
        friday_date = (t - timedelta(days=wd - 4)).isoformat()
        if state.get("friday") != friday_date:
            friday(dry)
            state["friday"] = friday_date
            save_state(state, dry)


def names(user_ids, cache={}):
    """Slack user IDs -> display names, for the timeline page (it shows names, never IDs)."""
    out = []
    for u in user_ids:
        if u not in cache:
            q = "https://slack.com/api/users.info?user=" + u
            req = urllib.request.Request(q, headers={"Authorization": "Bearer " + TOKEN})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            p = (r.get("user") or {}).get("profile") or {}
            cache[u] = (p.get("display_name") or r.get("user", {}).get("real_name") or "Someone") if r.get("ok") else "Someone"
        out.append(cache[u])
    return out


def export(path="team-timeline-data.json"):
    """Write the board as JSON for Matt's timeline page (team-timeline.html).
    Leaves out Details, Files and Watchers. Only rewrites the file when a task changed,
    so the page's 'updated' time is the last time something on the board moved."""
    t = today_local()
    local = lambda dt: (dt - timedelta(hours=5)).date()  # Cancun: UTC-5 all year
    iso = lambda d: d.isoformat() if d else None
    tasks = []
    for r in rows():
        if not r["task"]:
            continue
        tasks.append({
            "id": r["id"], "task": r["task"],
            "product": PRODUCT.get(r["product"], ""), "status": r["status"] or "to_do",
            "type": r["type"], "urgent": r["urgent"],
            "owner": names(r["owner"]), "waiting": names(r["waiting"]),
            "created": iso(local(r["created"])), "due": iso(r["due"]), "done_on": iso(r["done_on"]),
            "week_of": iso(r["week_of"]), "when": r["when"], "carried": r["carried"], "hours": r["hours"],
        })
    tasks.sort(key=lambda x: (x["owner"][:1] or ["~"], x["due"] or "9999", x["task"]))
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    if old.get("tasks") == tasks:
        print("export: no change on the board, file left as it was")
        return
    data = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "today": t.isoformat(),
            "board_url": BOARD_URL, "tasks": tasks}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"export: wrote {len(tasks)} tasks to {path}")


if __name__ == "__main__":
    job, dry = sys.argv[1], "--dry" in sys.argv
    if job == "link":
        post(f":clipboard: Team board: <{BOARD_URL}|open it here>", dry)
    elif job == "sync":
        sync(dry)
    elif job == "morning":
        sync(dry)
        morning(dry)
    elif job == "monday":
        sync(dry)
        roll(dry)
        sync(dry)  # again, so Week of follows the tasks the roll just moved into this week
        morning(dry, monday=True)
    elif job == "friday":
        sync(dry)
        friday(dry)
    elif job == "auto":
        auto(dry)
    elif job == "export":
        export()
    else:
        sys.exit("unknown job " + job)
