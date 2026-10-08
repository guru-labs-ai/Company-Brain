"""Builds the 2-week sprint report and the monthly overview from live ClickUp.

WHY THIS EXISTS. Both reports used to be written by a cloud routine (two-week) or
by hand (monthly). The routine ran in a sandbox that cannot reach ClickUp and lost
its GitHub login, so from mid August it fired every Monday and delivered nothing,
and the monthly page sat on May 2026. Nobody was told. This script runs inside a
GitHub Action instead, which has normal network access and its own push token.

WHAT IT WRITES. Only facts counted from ClickUp at run time: sprint totals, status
splits, per-product and per-person counts, what was finished, what is blocked.
No hand-written commentary, because commentary is what went stale last time (the
old page still named people who have left). If ClickUp cannot be read the script
exits non-zero and leaves the old pages untouched, so a failure is loud, never a
page full of zeros.

The pages are public GitHub Pages, so task names are only listed for finished and
blocked work, and anything tagged "matt" or mentioning a dollar amount is counted
but never named.

Needs CLICKUP_API_KEY in the environment. Python standard library only.
"""

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

TEAM_ID = "90132046592"
SPRINT_FOLDER_ID = "901310760791"  # "Active Weekly Sprints"
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "."

KEY = os.environ.get("CLICKUP_API_KEY", "").strip()
if not KEY:
    sys.exit("CLICKUP_API_KEY is not set")

# Tag (lower case) -> product. First match in this order wins, so a task tagged
# both "drm" and "marketing" counts under DRM.
PRODUCT_TAGS = [
    ("drm", "DRM"),
    ("ai sponsor", "AI Sponsor"),
    ("qa guru", "QA Guru"),
    ("qa-guru", "QA Guru"),
    ("rekindle", "Rekindle"),
    ("conscious ninjas", "Conscious AI Academy"),
    ("upworklt", "Upwork LT"),
    ("audos-black-webinar", "Webinars and funnels"),
    ("ghl", "Webinars and funnels"),
    ("guru-labs-site", "Webinars and funnels"),
    ("hyros", "Tracking and Route B"),
    ("claude", "Company Brain"),
    ("marketing", "Marketing"),
    ("operation", "Operations"),
]
# Fallback when no tag matched: a product name anywhere in the task title.
NAME_WORDS = [
    (r"\bdrm\b", "DRM"), (r"ai sponsor", "AI Sponsor"), (r"qa guru", "QA Guru"),
    (r"\brekin", "Rekindle"), (r"conscious ai academy|\bcaia\b", "Conscious AI Academy"),
    (r"webinar|funnel|\bghl\b", "Webinars and funnels"), (r"upwork", "Upwork LT"),
    (r"hyros|route b", "Tracking and Route B"), (r"company brain", "Company Brain"),
]
UNTAGGED = "Not tagged to a product"
PRIVATE_TAGS = {"matt"}
# These pages are public. Names matching these are counted but never shown.
PRIVATE_WORDS = re.compile(r"\$|\bcard\b|inbox|password|1password|salary|refund|invoice|payment|seats?\b|"
                           r"subscription|bank|personal|email management", re.I)
HIDE_GROUPS = {UNTAGGED, "Operations"}


def api(path, tries=4):
    url = "https://api.clickup.com/api/v2/" + path
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Authorization": KEY})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"ClickUp refused the key ({e.code}). Rotate CLICKUP_API_KEY.")
            if i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
        time.sleep(3 * (i + 1))


def sprint_lists():
    lists = []
    for archived in ("false", "true"):
        for l in api(f"folder/{SPRINT_FOLDER_ID}/list?archived={archived}").get("lists", []):
            m = re.match(r"Sprint (\d+)", l["name"])
            if m and l.get("start_date") and l.get("due_date"):
                lists.append({
                    "id": l["id"], "num": int(m.group(1)), "name": l["name"],
                    "start": int(l["start_date"]), "end": int(l["due_date"]),
                })
    uniq = {l["id"]: l for l in lists}
    return sorted(uniq.values(), key=lambda l: l["start"])


def list_tasks(list_id):
    out, page = [], 0
    while True:
        d = api(f"list/{list_id}/task?include_closed=true&subtasks=true&page={page}")
        t = d.get("tasks", [])
        out += t
        if len(t) < 100 or d.get("last_page"):
            return out
        page += 1


def bucket(task):
    s = task["status"]
    name, typ = s["status"].lower(), s.get("type")
    if typ in ("done", "closed"):
        return "done"
    if "block" in name:
        return "blocked"
    if "hold" in name:
        return "hold"
    if typ == "open" or name in ("to do", "backlog", "open"):
        return "todo"
    return "active"  # in progress, in review and other custom working states


def product(task):
    tags = [t["name"].lower() for t in task.get("tags", [])]
    for tag, prod in PRODUCT_TAGS:
        if tag in tags:
            return prod
    name = task["name"].lower()
    for pat, prod in NAME_WORDS:
        if re.search(pat, name):
            return prod
    return UNTAGGED


def is_private(task):
    tags = {t["name"].lower() for t in task.get("tags", [])}
    return bool(tags & PRIVATE_TAGS) or bool(PRIVATE_WORDS.search(task["name"])) or product(task) in HIDE_GROUPS


def people(task):
    return [a.get("username") or a.get("email", "?") for a in task.get("assignees", [])]


def fmt_day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%b %-d") if os.name != "nt" \
        else dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%b %#d")


def esc(s):
    return html.escape(str(s), quote=True)


def summarise(sprint, tasks):
    counts = {"done": 0, "active": 0, "todo": 0, "blocked": 0, "hold": 0}
    for t in tasks:
        counts[bucket(t)] += 1
    total = len(tasks)
    return {
        **sprint, "tasks": tasks, "counts": counts, "total": total,
        "pct": round(100 * counts["done"] / total) if total else 0,
    }


# ---------------------------------------------------------------- page parts

CSS = """
:root{--bg:#0b1220;--panel:#121b2e;--panel2:#18233a;--line:#25324d;--ink:#e8eef8;--muted:#93a3bd;
--green:#34d399;--yellow:#fbbf24;--red:#f87171;--blue:#60a5fa;--violet:#a78bfa;color-scheme:dark}
@media (prefers-color-scheme: light){:root{--bg:#f3f5f9;--panel:#ffffff;--panel2:#f7f9fc;--line:#d9e0eb;
--ink:#0f1a2b;--muted:#54627a;--green:#0f8a5f;--yellow:#a26a00;--red:#c0392b;--blue:#2563eb;--violet:#6d4fd8;color-scheme:light}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:22px 16px 60px;display:flex;flex-direction:column;gap:22px}
h1{font-size:24px;margin:0;line-height:1.2}h2{font-size:17px;margin:0 0 10px}
.sub{color:var(--muted);font-size:13px;margin-top:6px}.live{color:var(--green);font-weight:600}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:26px;line-height:1.1;font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:12.5px}
.g{color:var(--green)}.y{color:var(--yellow)}.r{color:var(--red)}.b{color:var(--blue)}.v{color:var(--violet)}
.sprints{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.bar{height:8px;background:var(--panel2);border-radius:99px;overflow:hidden;margin:10px 0 6px}.bar i{display:block;height:100%;background:var(--green)}
.meta{display:flex;justify-content:space-between;color:var(--muted);font-size:12.5px;gap:8px;flex-wrap:wrap}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.chip{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--panel2);border:1px solid var(--line)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600;font-size:12px}
ul.items{margin:0;padding-left:18px}ul.items li{margin:3px 0}.who{color:var(--muted);font-size:12.5px}
.prod{font-weight:600;margin:12px 0 4px}.note{color:var(--muted);font-size:12.5px}
"""


def page(title, subtitle, body, generated):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="report-generated" content="{generated.isoformat()}">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><h1>{esc(title)}</h1>
<div class="sub">{subtitle} · <span class="live">Updated {generated.strftime('%d %b %Y, %H:%M')} UTC</span> from live ClickUp</div></header>
{body}
<p class="note">Built automatically every day from ClickUp sprint lists. Done means a done or closed status. Active means in progress or in review. Task names are shown only for finished and blocked work; private items are counted but not named. If this date is more than a day old, the update has failed and Mariam gets a Slack alert.</p>
</div></body></html>
"""


def kpi_row(items):
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><b class="{c}">{v}</b><span>{esc(l)}</span></div>' for v, l, c in items) + "</div>"


def sprint_card(s, now_ms):
    state = "In progress" if s["start"] <= now_ms <= s["end"] else ("Closed" if s["end"] < now_ms else "Upcoming")
    c = s["counts"]
    label = "done so far" if state == "In progress" else "done"
    return f"""<div class="panel"><h2>Sprint {s['num']} <span class="note">{fmt_day(s['start'])} to {fmt_day(s['end'])} · {state}</span></h2>
<div class="bar"><i style="width:{s['pct']}%"></i></div>
<div class="meta"><span>{s['total']} tasks</span><span class="g">{s['pct']}% {label}</span></div>
<div class="chips"><span class="chip g">{c['done']} done</span><span class="chip y">{c['active']} active</span>
<span class="chip">{c['todo']} to do</span><span class="chip r">{c['blocked']} blocked</span><span class="chip">{c['hold']} on hold</span></div></div>"""


def product_table(tasks):
    rows = {}
    for t in tasks:
        r = rows.setdefault(product(t), {"done": 0, "active": 0, "todo": 0, "blocked": 0, "hold": 0})
        r[bucket(t)] += 1
    order = sorted(rows.items(), key=lambda kv: -sum(kv[1].values()))
    body = "".join(
        f"<tr><td>{esc(p)}</td><td class='g'>{r['done']}</td><td class='y'>{r['active']}</td><td>{r['todo']}</td>"
        f"<td class='r'>{r['blocked'] + r['hold']}</td><td>{sum(r.values())}</td></tr>" for p, r in order)
    return ('<div class="panel"><h2>By product</h2><div class="scroll"><table><tr><th>Product</th><th>Done</th>'
            '<th>Active</th><th>To do</th><th>Blocked / hold</th><th>Total</th></tr>' + body + "</table></div></div>")


def people_table(tasks):
    rows = {}
    for t in tasks:
        for p in people(t) or ["Unassigned"]:
            r = rows.setdefault(p, {"done": 0, "open": 0})
            r["done" if bucket(t) == "done" else "open"] += 1
    order = sorted(rows.items(), key=lambda kv: (-kv[1]["done"], -kv[1]["open"]))
    body = "".join(f"<tr><td>{esc(p)}</td><td class='g'>{r['done']}</td><td>{r['open']}</td></tr>" for p, r in order)
    return ('<div class="panel"><h2>By person</h2><div class="scroll"><table><tr><th>Person</th><th>Done</th>'
            '<th>Still open</th></tr>' + body + "</table></div></div>")


def item_list(title, tasks, limit_per_product=12):
    groups = {}
    hidden = 0
    for t in tasks:
        if is_private(t):
            hidden += 1
            continue
        groups.setdefault(product(t), []).append(t)
    if not groups and not hidden:
        return f'<div class="panel"><h2>{esc(title)}</h2><p class="note">None.</p></div>'
    out = [f'<div class="panel"><h2>{esc(title)}</h2>']
    for prod_name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(f'<div class="prod">{esc(prod_name)} <span class="note">({len(items)})</span></div><ul class="items">')
        for t in items[:limit_per_product]:
            who = ", ".join(people(t))
            out.append(f'<li>{esc(t["name"])}' + (f' <span class="who">· {esc(who)}</span>' if who else "") + "</li>")
        if len(items) > limit_per_product:
            out.append(f'<li class="note">and {len(items) - limit_per_product} more</li>')
        out.append("</ul>")
    if hidden:
        out.append(f'<p class="note">{hidden} more counted but not named here: admin, untagged or private items.</p>')
    out.append("</div>")
    return "".join(out)


def dedupe(tasks):
    seen, out = set(), []
    for t in tasks:
        if t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
    return out


def build():
    now = dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    sprints = sprint_lists()
    if not sprints:
        sys.exit("No sprint lists found in ClickUp")
    started = [s for s in sprints if s["start"] <= now_ms]
    current = started[-1] if started else sprints[0]
    idx = sprints.index(current)

    two = [summarise(s, list_tasks(s["id"])) for s in sprints[max(0, idx - 1): idx + 1]]
    month = [summarise(s, list_tasks(s["id"])) for s in sprints[max(0, idx - 3): idx + 1]]
    if sum(s["total"] for s in two) == 0:
        sys.exit("ClickUp returned zero tasks for the current sprints; refusing to publish an empty report")

    # ---------- 2-week report
    tasks2 = dedupe([t for s in two for t in s["tasks"]])
    cur = two[-1]
    done2 = [t for t in tasks2 if bucket(t) == "done"]
    blocked2 = [t for t in tasks2 if bucket(t) in ("blocked", "hold")]
    contributors = {p for t in tasks2 for p in people(t)}
    span = f"Sprint {two[0]['num']}" + (f" + Sprint {two[-1]['num']}" if len(two) > 1 else "") + \
        f" · {fmt_day(two[0]['start'])} to {fmt_day(two[-1]['end'])}, {dt.datetime.fromtimestamp(two[-1]['end'] / 1000, dt.timezone.utc).year}"
    body2 = "".join([
        kpi_row([
            (len(tasks2), "Tasks tracked", "v"), (len(done2), "Done", "g"),
            (sum(1 for t in tasks2 if bucket(t) == "active"), "Active", "y"),
            (f"{cur['pct']}%", f"Sprint {cur['num']} done so far", "b"),
            (len(contributors), "People with tasks", "v"), (len(blocked2), "Blocked or on hold", "r"),
        ]),
        '<div class="sprints">' + "".join(sprint_card(s, now_ms) for s in two) + "</div>",
        product_table(tasks2),
        item_list("Finished in these two sprints", done2),
        item_list("Blocked or on hold", blocked2),
        people_table(tasks2),
    ])
    write("two-week-report.html", page("Guru Labs, 2-Week Sprint Report", esc(span) + " · For Matt", body2, now))

    # ---------- monthly overview
    tasksm = dedupe([t for s in month for t in s["tasks"]])
    donem = [t for t in tasksm if bucket(t) == "done"]
    trend = "".join(
        f"<tr><td>Sprint {s['num']} <span class='note'>{fmt_day(s['start'])} to {fmt_day(s['end'])}</span></td>"
        f"<td>{s['total']}</td><td class='g'>{s['counts']['done']}</td><td class='y'>{s['counts']['active']}</td>"
        f"<td class='r'>{s['counts']['blocked'] + s['counts']['hold']}</td><td>{s['pct']}%</td></tr>" for s in month)
    created_in_window = sum(1 for t in tasksm if int(t.get("date_created") or 0) >= month[0]["start"])
    spanm = f"Sprints {month[0]['num']} to {month[-1]['num']} · {fmt_day(month[0]['start'])} to {fmt_day(month[-1]['end'])}"
    bodym = "".join([
        kpi_row([
            (len(tasksm), "Tasks across the month", "v"), (len(donem), "Done", "g"),
            (created_in_window, "Created this month", "b"),
            (sum(1 for t in tasksm if bucket(t) in ("blocked", "hold")), "Blocked or on hold", "r"),
            (len({p for t in tasksm for p in people(t)}), "People with tasks", "v"),
        ]),
        '<div class="panel"><h2>Sprint by sprint</h2><div class="scroll"><table><tr><th>Sprint</th><th>Tasks</th>'
        '<th>Done</th><th>Active</th><th>Blocked / hold</th><th>Done %</th></tr>' + trend + "</table></div>"
        '<p class="note">A task carried into the next sprint is counted in each sprint it sits in, and once in the month totals. The current sprint is still running.</p></div>',
        product_table(tasksm),
        item_list("Finished this month", donem, limit_per_product=15),
        people_table(tasksm),
    ])
    write("monthly-overview.html", page("Guru Labs, Monthly Overview", esc(spanm) + " · For the KPI review on the 15th", bodym, now))

    status = {
        "generated_at": now.isoformat(),
        "two_week": {"sprints": [s["num"] for s in two], "tasks": len(tasks2), "done": len(done2)},
        "monthly": {"sprints": [s["num"] for s in month], "tasks": len(tasksm), "done": len(donem)},
    }
    write("reports-status.json", json.dumps(status, indent=2) + "\n")
    print(json.dumps(status))


def write(name, text):
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    build()
