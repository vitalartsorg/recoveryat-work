#!/usr/bin/env python3
"""Weekly refresh for recoveryat.work.

Collects online meetings from the public directories of
  - Workaholics Anonymous (TSML JSON feed)
  - Underearners Anonymous (public Google Calendar .ics feed)
  - Debtors Anonymous (virtual meeting search + each group's detail page)
then rebuilds index.html from src/template.html.

Standard library only. If a source fails, or returns far fewer meetings than
last time, that fellowship's previous listings are kept and the problem is
written to refresh-warnings.txt so the workflow can flag it.
"""
import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
DATA_FILE = SRC / 'meetings.json'
WARN_FILE = ROOT / 'refresh-warnings.txt'

WA_FEED = 'https://workaholics-anonymous.org/wp-content/meetings/sfdc_transformed_json_n8n_no_unscheduled_FINAL.json'
WA_PAGE = 'https://workaholics-anonymous.org/meetings/'
UA_ICS = 'https://calendar.google.com/calendar/ical/underearnersanonymouscalendar%40gmail.com/public/basic.ics'
# The bare search URL is a redirect page; asking for a time zone returns the full table.
DA_SEARCH = 'https://debtorsanonymous.org/meeting-search-virtual/?mytimezone=America%2FNew_York'
DA_DETAIL = 'https://debtorsanonymous.org/meeting-search-detail/?mid={mid}&src=share'

UA_TZ = 'America/New_York'
KEEP_RATIO = 0.6        # keep last week's data if a source returns fewer than 60% as many meetings
DA_DELAY = 1.5          # seconds between DA detail-page requests
UA_AGENT = 'recoveryat.work weekly meeting-list refresh (independent listing; contact via the site)'
DAYS = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']

warnings = []


def warn(msg):
    print('WARNING:', msg, file=sys.stderr)
    warnings.append(msg)


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA_AGENT, 'Accept-Language': 'en'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f'could not fetch {url}: {last}')


def text_of(fragment):
    """HTML -> plain text with ' | ' between blocks."""
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', fragment)
    s = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h\d|tr|td)>', ' | ', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(html.unescape(s)).replace('#x28;', '(').replace('#x29;', ')')
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------- tagging
TAG_RULES = [
    ('Women', r"\bwom[ae]n|\bladies|\bfemmes?\b"),
    ('Men', r"\bmen'?s?\b|\bguys\b|\bdads\b|\bstag\b"),
    ('LGBTQ+', r"lgbt|\bgay\b|lesbian|rainbow|queer|\btrans\b"),
    ('BIPOC', r"black in|\bblacks in|bipoc|of color|\btwoc\b|\bpoc\b"),
    ('Beginners', r"beginner|newcomer"),
    ('Step Study', r"\bsteps?\b|step study|12\+12|stepsponsor|pasos|passos|étapes|etapes"),
    ('Literature', r"literature|big book|book|libro|livro|reading|course of hope|kurs der"),
    ('Speaker', r"speaker|qualifier"),
    ('Meditation', r"meditat|prayer|eleventh|medatation|\b11th\b"),
    ('Business', r"\bbda\b|\bbua\b|business|entrepreneur|empres|coworking|self-employed|startup|construction"),
    ('Writing', r"writing|writers"),
    ('Visions', r"vision"),
]
LANG_NAME = [
    ('Portuguese', r"reunião|reuniao|devedores|próspera|prosperidade|caminho para|serenidade|estudo|moema|jardins|quinta feira|equilíbrio|dabh|livro"),
    ('Spanish', r"español|espanol|spanish|pasos|grupo|desde méxico|avanzando|\bsuba\b|estudio del|serenidad|prosperidad|espacio|recuperaci|conciencia|despertar|abundancia|solvencia|la redonda|bogotá|autofinanzas|intergrupo"),
    ('French', r"français|francais|francophone|\bsga\b|groupe|nouveau départ|rétablissement|retablissement|espoir|dimanche|pyramides|héritages|heritages"),
    ('Russian', r"russian|dobroe|blagopoluchie|byt'|svoboda|creator \(russian"),
    ('German', r"german|ein kurs|sonntagabend|samstag"),
    ('Italian', r"italia|italian|risveglio|sottoguadagnatori|riunioni"),
    ('Hindi', r"hindi"), ('Hebrew', r"hebrew"), ('Farsi', r"farsi|persian|mohajeran"),
    ('Polish', r"polish|polsk"), ('Swedish', r"en ny dag"), ('Icelandic', r"icelandic"),
]


def tags_from(text):
    low = text.lower()
    return [t for t, rx in TAG_RULES if re.search(rx, low)]


def lang_from(text):
    low = text.lower()
    if 'english' in low:
        return 'English'
    for lang, rx in LANG_NAME:
        if re.search(rx, low):
            return lang
    return 'English'


def irregular(name):
    return bool(re.search(r"\b(1st|2nd|3rd|4th|5th|last|3ème)\b|pop-up|monthly|twice a month", name, re.I))


def add(tags, t):
    if t not in tags:
        tags.append(t)


# ---------------------------------------------------------------- join details
PC_BAD = re.compile(r'^(for|protected\.?|\(no|\.|n/a|none|no|please|host|contact|needed\.?|required|the|sin|#|is|email|if|will|not|-)$', re.I)


def clean_pc(v):
    v = (v or '').strip().rstrip('.,;)').replace('#success', '')
    if not v or PC_BAD.match(v) or len(v) >= 20 or v.endswith('.1') or '=' in v:
        return ''
    return v


def zoom_id(text):
    m = re.search(r'zoom\.us/(?:j|w)/(\d{9,11})', text, re.I)
    if m:
        return m.group(1)
    m = re.search(r'(?:meeting|conference|reunión|riunione|réunion)[ -]?(?:id|#)\s*(?:is|:)?\s*:?\s*(\d[\d \-]{7,14}\d)', text, re.I)
    if m:
        d = re.sub(r'\D', '', m.group(1))
        if 9 <= len(d) <= 11:
            return d
    return ''


def passcode(text):
    m = re.search(r'(?:passcode|password|pass code|pwd|mot de passe|kenncode|codice d.accesso|jelszó/password)\s*(?:is|:)?\s*:?\s*([^\s|,]+)', text, re.I)
    return clean_pc(m.group(1)) if m else ''


PHONE_RX = re.compile(
    r'(?:call[- ]?in|dial(?:[- ]?(?:in|up))?|phone)(?: number)?[^:|\d+(]{0,30}:?\s*'
    r'(\+?\(?\+?\d[\d\s().\-]{8,17}\d)', re.I)


def phone(text):
    m = PHONE_RX.search(text)
    if not m:
        return ''
    raw = m.group(1)
    d = re.sub(r'\D', '', raw)
    if raw.lstrip('(').startswith('+'):
        if d.startswith('1') and len(d) == 11:
            return f'+1 {d[1:4]} {d[4:7]} {d[7:]}'
        return '+' + d
    if len(d) == 10:
        return f'+1 {d[:3]} {d[3:6]} {d[6:]}'
    if len(d) == 11 and d.startswith('1'):
        return f'+1 {d[1:4]} {d[4:7]} {d[7:]}'
    return ''


def access_code(text):
    m = re.search(r'(?<![Pp]ass)(?<![Pp]ass )(?:access code|participant code|call code|code)\s*(?:is|:)?\s*:?\s*(\d[\d ]{2,10}#?)', text, re.I)
    return ('Access code ' + m.group(1).replace(' ', '')) if m else ''


def email_only_note(text, where):
    if re.search(r'e-?mail|contact', text, re.I):
        return f'Email or contact the group for the link (details on {where}).'
    return ''


def join_fields(m, text, where):
    zid, pc, tel, code = zoom_id(text), passcode(text), phone(text), access_code(text)
    if zid:
        m['zid'] = zid
        if m['p'] in ('Video', ''):
            m['p'] = 'Zoom'
    if pc:
        m['pc'] = pc
    if tel:
        m['tel'] = tel
        m['ph'] = True
        if code:
            m['code'] = code
        elif zid:
            m['code'] = f'Meeting ID {zid}#' + (f', passcode {pc}#' if pc else '')
    if not zid and not tel:
        note = email_only_note(text, where)
        if note:
            m['note'] = note


# ---------------------------------------------------------------- WA
WA_LANG = {'S': 'Spanish', 'FR': 'French', 'RUS': 'Russian', 'POL': 'Polish', 'HU': 'Hungarian',
           'WA_Czech': 'Czech/Slovak', 'SK': 'Czech/Slovak', 'DE': 'German', 'EN': 'English'}
WA_HOSTS = {'zoom.us': 'Zoom', 't.me': 'Telegram', 'meet.google.com': 'Google Meet', 'skype.com': 'Skype',
            'signal.org': 'Signal', 'whatsapp.com': 'WhatsApp', 'webex.com': 'Webex'}


def get_wa():
    feed = json.loads(fetch(WA_FEED))
    out = []
    for r in feed:
        url, tel_raw = r.get('conference_url') or '', r.get('conference_phone') or ''
        if not (url or tel_raw):
            continue
        days = r.get('day')
        days = days if isinstance(days, list) else [days]
        days = sorted({int(x) for x in days if str(x).strip().isdigit()})
        t = (r.get('time') or '')[:5]
        if not days or not re.fullmatch(r'\d\d:\d\d', t):
            continue
        name = (r.get('name') or '').strip()
        ty = [x for x in (r.get('types') or []) if x]
        langs = [WA_LANG[x] for x in ty if x in WA_LANG and x != 'EN']
        host = re.sub(r'^https?://([^/]+).*$', r'\1', url).lower()
        plat = next((v for k, v in WA_HOSTS.items() if host.endswith(k)), 'Video' if url else 'Phone')
        tags = tags_from(name)
        if '12x12' in ty: add(tags, 'Step Study')
        if 'LIT' in ty or 'WA_Book_of_Recovery' in ty: add(tags, 'Literature')
        if 'SP' in ty: add(tags, 'Speaker')
        if 'WA_business_owners_enetrpreneurs' in ty: add(tags, 'Business')
        if 'WA_work_avoidance_anorexia' in ty: add(tags, 'Work avoidance')
        if 'C' in ty: add(tags, 'Closed')
        end = (r.get('end_time') or '')[:5] or None
        m = dict(f='WA', n=name, d=days, t=t, e=end, z=r.get('timezone') or 'UTC', p=plat,
                 ph=bool(tel_raw), l=langs[0] if langs else 'English', g=tags, s=r.get('slug') or '')
        if irregular(name):
            m['irr'] = 1
        notes = ' | '.join(str(r.get(k) or '') for k in ('conference_url_notes', 'conference_phone_notes', 'notes'))
        join_fields(m, url + ' | ' + notes, "WA's listing")
        dial = re.match(r'\s*(\+?[\d\s().\-]{10,20})(?:,,(\d+)#?)?\s*(?:,+\*?(\w+)#?)?', tel_raw)
        if dial:
            num = phone('phone: ' + dial.group(1))
            if num:
                m['tel'] = num
                m['ph'] = True
                if dial.group(2):
                    code = f"Meeting ID {dial.group(2)}#"
                    if dial.group(3):
                        code += f", passcode {dial.group(3)}#"
                    m['code'] = code
                    m.setdefault('zid', dial.group(2))
        out.append(m)
    return out


# ---------------------------------------------------------------- UA (ICS)
def ics_events(raw):
    raw = re.sub(r'\r?\n[ \t]', '', raw)
    for block in raw.split('BEGIN:VEVENT')[1:]:
        block = block.split('END:VEVENT')[0]
        ev = {}
        for line in block.splitlines():
            if ':' not in line:
                continue
            key, val = line.split(':', 1)
            name, *params = key.split(';')
            if name not in ev:
                ev[name] = (val, dict(p.split('=', 1) for p in params if '=' in p))
        yield ev


def ics_text(v):
    return v.replace('\\n', ' | ').replace('\\N', ' | ').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\')


def ics_dt(val, params):
    if val.endswith('Z'):
        return dt.datetime.strptime(val, '%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    if 'T' not in val:
        return None
    tz = ZoneInfo(params.get('TZID', UA_TZ))
    return dt.datetime.strptime(val[:15], '%Y%m%dT%H%M%S').replace(tzinfo=tz)


def get_ua():
    raw = fetch(UA_ICS)
    today = dt.datetime.now(ZoneInfo(UA_TZ)).date()
    et = ZoneInfo(UA_TZ)
    best = {}
    for ev in ics_events(raw):
        if 'RRULE' not in ev or 'RECURRENCE-ID' in ev or ev.get('STATUS', ('',))[0] == 'CANCELLED':
            continue
        rule = dict(p.split('=', 1) for p in ev['RRULE'][0].split(';') if '=' in p)
        until = rule.get('UNTIL', '')[:8]
        if until and until < today.strftime('%Y%m%d'):
            continue
        if 'COUNT' in rule:
            continue
        start = ics_dt(*ev.get('DTSTART', ('', {})))
        end = ics_dt(*ev.get('DTEND', ('', {}))) if 'DTEND' in ev else None
        if not start:
            continue
        freq = rule.get('FREQ')
        irr = freq not in ('WEEKLY', 'DAILY') or rule.get('INTERVAL', '1') != '1'
        if freq == 'DAILY':
            wanted = list(range(7))
        else:
            byday = rule.get('BYDAY') or DAYS[(start.weekday() + 1) % 7]
            wanted = []
            for token in byday.split(','):
                mm = re.fullmatch(r'([+-]?\d)?([A-Z]{2})', token)
                if mm and mm.group(2) in DAYS:
                    wanted.append(DAYS.index(mm.group(2)))
                    if mm.group(1):
                        irr = True
        dur = int((end - start).total_seconds() // 60) if end else 60
        src_tz = start.tzinfo
        local = start.astimezone(src_tz) if not isinstance(src_tz, dt.timezone) else start.astimezone(et)
        summary = ics_text(ev.get('SUMMARY', ('',))[0]).strip()
        desc = text_of(ics_text(ev.get('DESCRIPTION', ('',))[0]))
        loc = ics_text(ev.get('LOCATION', ('',))[0]).strip()
        modified = ev.get('LAST-MODIFIED', ('',))[0]
        for wd in wanted:
            # next date with this weekday in the event's own zone, then convert to Eastern
            base = dt.datetime.now(local.tzinfo).date()
            delta = (wd - (base.weekday() + 1) % 7) % 7
            day = base + dt.timedelta(days=delta)
            occ = dt.datetime.combine(day, local.time().replace(tzinfo=None), tzinfo=local.tzinfo).astimezone(et)
            occ_end = occ + dt.timedelta(minutes=dur)
            key = (summary, occ.strftime('%H:%M'), (occ.weekday() + 1) % 7)
            if key in best and best[key]['mod'] >= modified:
                continue
            best[key] = dict(mod=modified, wd=(occ.weekday() + 1) % 7, t=occ.strftime('%H:%M'),
                             e=occ_end.strftime('%H:%M'), summary=summary, desc=desc, loc=loc, irr=irr)
    # merge weekdays of the same meeting
    merged = {}
    for b in best.values():
        k = (b['summary'], b['t'], b['e'], b['loc'], b['desc'][:200])
        merged.setdefault(k, {**b, 'days': set()})['days'].add(b['wd'])
    out = []
    for b in merged.values():
        name = re.sub(r'\s*\*?pop-?up\*?\s*', ' (pop-up) ', b['summary'], flags=re.I).strip()
        name = re.sub(r'^(VIDEO|PHONE/VIDEO)\s*(MEETING)?\s*:\s*', '', name, flags=re.I).strip()
        low = (b['loc'] + ' ' + b['summary']).lower()
        plat = ('Zoom' if 'zoom' in low else 'Phone' if 'phone' in low else 'Skype' if 'skype' in low
                else 'Google Meet' if 'google meet' in low else 'Video')
        tags = tags_from(name)
        if 'BUA' in name or 'business' in name.lower(): add(tags, 'Business')
        m = dict(f='UA', n=name, d=sorted(b['days']), t=b['t'], e=b['e'], z=UA_TZ, p=plat,
                 ph=plat == 'Phone', l=lang_from(name), g=tags)
        if b['irr'] or irregular(name):
            m['irr'] = 1
        if 'face-to-face' in low or 'in person' in low:
            m['hy'] = 1
        join_fields(m, b['desc'], "UA's calendar")
        out.append(m)
    return out


# ---------------------------------------------------------------- DA
DA_TZ = {
    'Pacific Time (US/CAN)': 'America/Los_Angeles', 'Pacific Time (BC)': 'America/Vancouver',
    'Eastern Time (US/CAN)': 'America/New_York', 'Central Time (US/CAN)': 'America/Chicago',
    'Mountain Time (US/CAN)': 'America/Denver', 'Mountain Standard Time (AZ)': 'America/Phoenix',
    'Hawaii-Aleutian Standard Time': 'Pacific/Honolulu', 'British Time': 'Europe/London',
    'Irish Time': 'Europe/Dublin', 'Central European Time': 'Europe/Paris', 'Western European Time': 'Europe/Lisbon',
    'Moscow Time': 'Europe/Moscow', 'Indian Standard Time': 'Asia/Kolkata', 'Brasilia BRT': 'America/Sao_Paulo',
    'Central Standard Time (Mexico)': 'America/Mexico_City', 'East Africa Time': 'Africa/Nairobi',
    'Central Africa Time': 'Africa/Johannesburg', 'Australia Eastern Time (NSW+Vic)': 'Australia/Sydney',
    'Australian Eastern Standard Time (Qsld)': 'Australia/Brisbane', 'Argentina Time': 'America/Argentina/Buenos_Aires',
    'Ecuador Time': 'America/Guayaquil', 'Columbia Time': 'America/Bogota', 'Israel Time': 'Asia/Jerusalem',
    'Aqtobe Time (Kazakhstan)': 'Asia/Almaty', 'Greenwich Mean Time (Iceland)': 'Atlantic/Reykjavik',
    'New Zealand Time': 'Pacific/Auckland', 'Japan Standard Time': 'Asia/Tokyo', 'Acre Time (Brazil)': 'America/Rio_Branco',
}
DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


def to24(t):
    m = re.match(r'(\d{1,2}):(\d\d)\s*([ap])m', t.strip(), re.I)
    if not m:
        return ''
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == 'p' else 0)
    return f'{h:02d}:{m.group(2)}'


def get_da():
    page = fetch(DA_SEARCH)
    table = re.search(r'(?is)<table[^>]*id=["\']result-table["\'].*?</table>', page)
    if not table:
        raise RuntimeError('DA search table not found (the page layout may have changed)')
    slots = {}   # mid -> {'days': set(), 't': .., 'tz': .., 'venue': .., 'loc': ..}
    for tr in re.findall(r'(?is)<tr\b.*?</tr>', table.group(0)):
        cells = re.findall(r'(?is)<td\b[^>]*>(.*?)</td>', tr)
        if len(cells) < 5:
            continue
        link = re.search(r'mid=(\d+)', tr)
        if not link:
            continue
        day, tm, venue, loc = (text_of(c) for c in (cells[0], cells[1], cells[3], cells[4]))
        mt = re.match(r'(\d{1,2}:\d\d\s*[ap]m)\s*(.*)$', tm, re.I)
        if day not in DAY_NAMES or not mt:
            continue
        tz = DA_TZ.get(mt.group(2).strip())
        if not tz:
            warn(f'DA: unknown time zone label "{mt.group(2).strip()}"')
            continue
        key = (link.group(1), to24(mt.group(1)), tz)
        s = slots.setdefault(key, {'days': set(), 'venue': venue, 'loc': loc})
        s['days'].add(DAY_NAMES.index(day))
    if not slots:
        raise RuntimeError('DA search returned no meetings')
    details = {}
    out = []
    for (mid, t, tz), s in slots.items():
        if mid not in details:
            time.sleep(DA_DELAY)
            try:
                page = fetch(DA_DETAIL.format(mid=mid))
            except RuntimeError as e:
                warn(str(e))
                details[mid] = ''
                continue
            body = text_of(page)
            i = body.find('Copy this link to share')
            j = body.find('Information last updated')
            details[mid] = body[i if i >= 0 else 0: j if j >= 0 else None]
        d = details[mid]
        hm = re.search(r'\|\s*([^|]{2,140}?)\s*\|\s*(Open|Closed)?\s*(B\.?D\.?A\.?|D\.?A\.?|Intergroup|GSR)\s+meeting\s*#\s*(\d+)', d, re.I)
        if not hm:
            continue
        name, kind, grp = hm.group(1).strip(), hm.group(3).upper().replace('.', ''), hm.group(4)
        if kind in ('INTERGROUP', 'GSR'):
            continue
        if 'Email' in s['venue'] and 'Video' not in s['venue'] and 'Phone' not in s['venue']:
            continue
        lang = (re.search(r'Primary language:\s*([A-Za-z ]+?)\s*\|', d) or [None, 'English'])[1].strip()
        focus = (re.search(r'Meeting focus:\s*([^|]+)', d) or [None, ''])[1]
        times = re.search(r'(\d\d:\d\d [ap]m) to (\d\d:\d\d [ap]m)', d, re.I)
        tags = tags_from(name + ' ' + kind + ' ' + focus)
        is_phone = 'Telephone venue' in d or ('Phone' in s['venue'] and 'Video' not in s['venue'])
        plat = re.search(r'(?:Video|Audio) platform:\s*([A-Za-z ]+?)\s*\|', d)
        plat = plat.group(1).strip().title() if plat else ('Phone' if is_phone else 'Video')
        m = dict(f='DA', k='BDA' if kind == 'BDA' else 'DA', n=name, d=sorted(s['days']), t=t,
                 e=to24(times.group(2)) if times else None, z=tz, p='Phone' if is_phone else plat,
                 ph=is_phone, l=lang if lang not in ('', 'Other') else 'English', g=tags,
                 loc=s['loc'], grp=grp, mid=mid)
        if 'Face-to-face' in s['venue'] or 'Hybrid' in d:
            m['hy'] = 1
        join_fields(m, d, "the group's DA page")
        out.append(m)
    return out


# ---------------------------------------------------------------- main
def main():
    prev = json.loads(DATA_FILE.read_text(encoding='utf-8')) if DATA_FILE.exists() else []
    result, kept = [], []
    for f, getter in (('WA', get_wa), ('UA', get_ua), ('DA', get_da)):
        old = [m for m in prev if m['f'] == f]
        try:
            new = getter()
        except Exception as e:  # noqa: BLE001
            warn(f'{f}: {e}. Kept last week\'s {len(old)} listings.')
            result += old
            kept.append(f)
            continue
        if old and len(new) < KEEP_RATIO * len(old):
            warn(f'{f}: only {len(new)} meetings found (last week {len(old)}). Kept last week\'s listings.')
            result += old
            kept.append(f)
            continue
        print(f'{f}: {len(new)} meetings (last week {len(old)})', file=sys.stderr)
        result += new
    result.sort(key=lambda m: (m['f'], m['n'].lower(), m['t']))
    DATA_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=0), encoding='utf-8')

    tpl = (SRC / 'template.html').read_text(encoding='utf-8')
    updated = dt.datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%B %-d, %Y')
    page = tpl.replace('/*__MEETINGS__*/[]', json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    page = page.replace('__UPDATED__', updated)
    (ROOT / 'index.html').write_text(page, encoding='utf-8')
    if warnings:
        WARN_FILE.write_text('\n'.join(warnings) + '\n', encoding='utf-8')
    elif WARN_FILE.exists():
        WARN_FILE.unlink()
    print(f'wrote index.html with {len(result)} meetings; kept old data for: {kept or "none"}', file=sys.stderr)


if __name__ == '__main__':
    main()
