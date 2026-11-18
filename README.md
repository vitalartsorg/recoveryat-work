# recoveryat.work

An independent listing of online Workaholics Anonymous, Underearners Anonymous
and Debtors Anonymous meetings, shown in each visitor's time zone.

## What's in here

| File | What it is |
|---|---|
| `index.html` | The website. Vercel serves this file. |
| `src/template.html` | The page design. The refresh pours meeting data into it. |
| `src/meetings.json` | The current meeting list. |
| `scripts/refresh.py` | Collects meetings from the three fellowships and rebuilds `index.html`. |
| `.github/workflows/refresh.yml` | Runs the refresh every Monday. |

## How the weekly refresh works

Every Monday at about 1:30 am Pacific, GitHub runs `scripts/refresh.py`. It reads:

- **WA:** the Meeting Finder data file on workaholics-anonymous.org
- **UA:** the public Google Calendar on underearnersanonymous.org
- **DA:** the Virtual Meeting Search and each group's detail page on debtorsanonymous.org (with a pause between pages)

It then rebuilds `index.html` and saves it here. Vercel publishes the change automatically.

If a fellowship's site is down, or returns far fewer meetings than last week,
that fellowship's listings from last week are kept, the run is marked as failed,
and GitHub emails you. The rest of the site still updates.

## Running it by hand

Go to the **Actions** tab → **Weekly meeting refresh** → **Run workflow**.

## If a run fails

Open the failed run and click **Flag any source that was skipped** (or the first
red step) to read the message. Copy it into a conversation with Claude along with
this folder, and ask for a fix.
