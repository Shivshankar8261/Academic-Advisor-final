"""
Exact answers from the two November 2026 files: the BMS (Hons.) Digital Business spread and the synthetic campus
infrastructure dataset. Counts and lists are SQL over courses.db (built by extract_extra.py), so a model never
counts rooms or adds up credits. Every answer states the file's own status: the BMS sheet is a draft with inferred
baskets, and the campus data is synthetic demo data, not the real campus.

    campus.route(question, message) -> [direct-answer chunk] or []   ([] = not one of these exact questions)
"""
import re

import structured
from structured import _chunk, _db, _direct, _num

BMS_FILE = "Semester_Spread_Structures_Sept_2026_with_BMS.xlsx"
INFRA_FILE = "College_Infrastructure_Dataset_SYNTHETIC.xlsx"
BMS_NOTE = ("*This BMS sheet is a draft built from Semester III, V and VII timetables. The basket placement is "
            "inferred, not official, so please confirm it with the BMS programme office.*")
INFRA_NOTE = "*This campus information comes from a synthetic demo dataset, not the real Vidyashilp campus.*"

BMS_RE = re.compile(r"\bb\.?\s?m\.?\s?s\b|\bdigital business\b|\bbachelor of management\b", re.I)
INFRA_RE = re.compile(
    r"\b(campus|infrastructure|blocks?|floors?|ground floor|rooms?|classrooms?|class rooms?|labs?|laboratory|"
    r"seats?|seating|capacity|air[- ]?condition\w*|\bac\b|cctv|cameras?|projectors?|displays?|touch[- ]?screens?|"
    r"computers?|desktops?|pcs?|wi-?fi|washrooms?|toilets?|restrooms?|lifts?|elevators?|staircases?|stairs|"
    r"drinking water|water dispensers?|fire extinguishers?|fire alarms?|smoke detectors?|first[- ]aid|"
    r"assembly points?|library|seminar halls?|lobby|lobbies|faculty offices?|hod cabins?|exam(ination)? cell|"
    r"principal'?s office|server room|equipment|facilit(y|ies)|maintenance|broken|not working|repair)\b", re.I)
ROOM_ID_RE = re.compile(r"\b([ABC])-?([0-3]\d\d)\b", re.I)
BLOCK_RE = re.compile(r"\bblock\s*([ABC])\b|\b([ABC])\s*block\b", re.I)
FLOOR_WORDS = {"ground": 0, "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
FLOOR_RE = re.compile(r"\b(ground|first|1st|second|2nd|third|3rd)\s+floor\b|\bfloor\s*([0-3])\b", re.I)
SEM_RE = re.compile(r"\b(?:semester|sem)\s*(\d|i{1,3}|iv|v|vi{0,3})\b|\b(\d)(?:st|nd|rd|th)\s+(?:semester|sem)\b", re.I)
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
COUNT_RE = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b|\btotal\b", re.I)
BROKEN_RE = re.compile(r"\b(not (fully )?(working|operational)|broken|faulty|under maintenance|maintenance|repair|"
                       r"out of order|damaged|partially)\b", re.I)
SUMMARY_RE = re.compile(r"\b(campus|infrastructure)\b.{0,30}\b(summary|overview|details|about|facilities)\b"
                        r"|\b(tell me about|describe|overview of|summary of)\b.{0,20}\b(campus|infrastructure|blocks?)\b", re.I)

# words a student uses -> room type in the Rooms sheet
ROOM_TYPES = [
    (r"class ?rooms?", "Classroom"), (r"computer labs?|labs?|laboratory", "Computer Lab"),
    (r"faculty offices?|staff rooms?", "Faculty Office"), (r"hod cabins?|hod rooms?|hod offices?", "HOD Cabin"),
    (r"seminar halls?|auditorium", "Seminar Hall"), (r"meeting rooms?", "Meeting Room"),
    (r"faculty meeting rooms?", "Faculty Meeting Room"), (r"faculty lounges?", "Faculty Lounge"),
    (r"lobb(y|ies)", "Lobby"), (r"library", "Library"), (r"first[- ]aid rooms?|sick rooms?", "First-Aid Room"),
    (r"server rooms?|network rooms?", "Server & Network Room"), (r"admin(istrative)? (spaces?|offices?)", "Administrative Space"),
    (r"record rooms?", "Department Record Room"), (r"pantr(y|ies)", "Faculty Pantry"),
]
# words -> facility type (Facilities sheet)
FACILITY_TYPES = [
    (r"washrooms?|toilets?|restrooms?|bathrooms?", "Washroom"), (r"drinking water|water dispensers?|water coolers?", "Drinking Water"),
    (r"lifts?|elevators?", "Lift"), (r"staircases?|stairs", "Staircase"), (r"fire extinguishers?", "Fire Extinguisher"),
    (r"smoke detectors?", "Smoke Detector"), (r"fire alarms?|call points?", "Fire Alarm"), (r"hose reels?", "Hose Reel"),
    (r"exit sign(age|s)?|emergency exits?", "Exit Signage"), (r"first[- ]aid (box|boxes|kits?)", "First Aid"),
    (r"assembly points?", "Assembly Point"),
]
# words -> equipment item (Equipment sheet); only items a student would plausibly ask about
ITEMS = [
    (r"projectors?", ["Projector"]), (r"desktops?|computers?(?! science)|pcs?", ["Desktop computer"]),
    (r"touch[- ]?screens?|interactive (displays?|screens?)|smart ?boards?|tvs?|display screens?", ["Interactive touch-screen display"]),
    (r"whiteboards?", ["Whiteboard"]), (r"printers?", ["Printer"]), (r"wi-?fi|access points?", ["Wi-Fi access point"]),
    (r"ceiling fans?|fans?", ["Ceiling fan"]), (r"ups\b", ["UPS"]), (r"air[- ]?conditioners?|ac units?", ["Air conditioner (split)", "Precision AC unit"]),
]


def _table(head: list[str], rows: list[list]) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join("-" if v is None else str(_num(v)) for v in r) + " |" for r in rows])


def _answer(text: str, file: str, section: str, note: str) -> list[dict]:
    answer = f"{text}\n\n{note}"
    return [_direct(_chunk(answer, file, section), answer)]


def _block(text: str) -> str | None:
    m = BLOCK_RE.search(text)
    return (m.group(1) or m.group(2)).upper() if m else None


def _floor(text: str) -> int | None:
    m = FLOOR_RE.search(text)
    if not m:
        return 0 if re.search(r"\bground\b", text, re.I) else None
    return FLOOR_WORDS[m.group(1).lower()] if m.group(1) else int(m.group(2))


def _where(block, floor) -> str:
    floor_name = ["the ground floor", "the first floor", "the second floor", "the third floor"]
    parts = ([f"Block {block}"] if block else []) + ([floor_name[floor]] if floor is not None else [])
    return " on ".join(parts) if parts else "the whole campus"


def _filters(block, floor, prefix="") -> tuple[str, list]:
    sql, args = "", []
    if block:
        sql += f" AND {prefix}block=?"
        args.append(block)
    if floor is not None:
        sql += f" AND {prefix}floor=?"
        args.append(floor if prefix != "f." else ("Ground" if floor == 0 else str(floor)))
    return sql, args


def _match(text: str, table: list) -> object:
    return next((value for rx, value in table if re.search(rf"\b(?:{rx})\b", text, re.I)), None)


# ----------------------------------------------------------------------------------------------- infrastructure
def infra_summary() -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM infra_summary ORDER BY source_row").fetchall()
        rules = [r["text"] for r in con.execute("SELECT text FROM infra_rules WHERE section='Infrastructure rules'")]
    table = _table(["Metric", "Block A", "Block B", "Block C", "Campus total"],
                   [[r["metric"], r["block_a"], r["block_b"], r["block_c"], r["total"]] for r in rows])
    text = ("**The campus has three blocks (A, B and C), each with a ground floor and three upper floors.**\n\n"
            + table + "\n\n**Rules in the dataset:**\n" + "\n".join(f"- {r}" for r in rules))
    return _answer(text, INFRA_FILE, "Summary sheet (block-wise summary and infrastructure rules)", INFRA_NOTE)


def room_detail(room_id: str) -> list[dict]:
    with _db() as con:
        room = con.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        if not room:
            return _answer(f"There is no room {room_id} in the dataset. Room numbers run A-001 to C-313.",
                           INFRA_FILE, "Rooms sheet", INFRA_NOTE)
        eq = con.execute("SELECT * FROM equipment WHERE room_id=? ORDER BY source_row", (room_id,)).fetchall()
    head = (f"**{room_id} is {room['name']}** ({room['type']}{', ' + room['department'] if room['department'] else ''}), "
            f"Block {room['block']}, {room['floor_name']}. Capacity {room['capacity']}; air conditioning: "
            f"{'yes, ' + str(room['ac_units']) + ' unit(s)' if room['ac'] == 'Yes' else 'no'}.")
    table = _table(["Item", "Quantity", "Working", "Status"],
                   [[e["item"], e["quantity"], e["operational_qty"], e["status"]] for e in eq])
    return _answer(f"{head}\n\n{table}", INFRA_FILE, f"Rooms and Equipment sheets, room {room_id}", INFRA_NOTE)


DEPARTMENTS = [(r"computer science|cse", "Computer Science & Engineering"), (r"information technology|\bit\b", "Information Technology"),
               (r"electronics|ece", "Electronics & Communication Engineering"), (r"mechanical", "Mechanical Engineering"),
               (r"math\w*|sciences", "Mathematics & Sciences"), (r"management", "Management Studies")]


def rooms_of_type(rtype: str, block, floor, count_only: bool, dept: str | None = None) -> list[dict]:
    sql, args = _filters(block, floor)
    if dept:
        sql += " AND department=?"
        args.append(dept)
    with _db() as con:
        rows = con.execute(f"SELECT * FROM rooms WHERE type=?{sql} ORDER BY room_id", [rtype, *args]).fetchall()
    where = _where(block, floor) + (f" ({dept})" if dept else "")
    if not rows:
        return _answer(f"**There are no rooms of type {rtype} in {where}** in the dataset.", INFRA_FILE, "Rooms sheet", INFRA_NOTE)
    seats = sum(r["capacity"] or 0 for r in rows)
    head = f"**{where[0].upper() + where[1:]} has {len(rows)} {rtype.lower()}{'' if len(rows) == 1 else 's'}**"
    head += f", with a total capacity of {seats}." if rtype in ("Classroom", "Computer Lab", "Seminar Hall") else "."
    if count_only and len(rows) > 12 and not block:
        per = {b: [r for r in rows if r["block"] == b] for b in "ABC"}
        table = _table(["Block", rtype + "s", "Capacity"], [[f"Block {b}", len(rs), sum(r["capacity"] or 0 for r in rs)]
                                                            for b, rs in per.items()])
    else:
        table = _table(["Room", "Name", "Floor", "Department", "Capacity", "AC"],
                       [[r["room_id"], r["name"], r["floor_name"], r["department"], r["capacity"], r["ac"]] for r in rows])
    if rtype == "Computer Lab":
        with _db() as con:
            pcs = con.execute(f"SELECT SUM(e.quantity) n, SUM(e.operational_qty) ok FROM equipment e JOIN rooms r "
                              f"USING(room_id) WHERE r.type='Computer Lab' AND e.item='Desktop computer'"
                              f"{_filters(block, floor, 'r.')[0]}", _filters(block, floor, 'r.')[1]).fetchone()
        head += f" The labs have {pcs['n']} desktop computers, of which {pcs['ok']} are working."
    return _answer(f"{head}\n\n{table}", INFRA_FILE, f"Rooms sheet, {rtype} rows", INFRA_NOTE)


def ac_rooms(block, floor) -> list[dict]:
    sql, args = _filters(block, floor)
    with _db() as con:
        rows = con.execute(f"SELECT * FROM rooms WHERE ac='Yes'{sql} ORDER BY room_id", args).fetchall()
    where = _where(block, floor)
    text = (f"**{len(rows)} room{'' if len(rows) == 1 else 's'} in {where} {'has' if len(rows) == 1 else 'have'} air "
            f"conditioning.** The dataset says AC is mainly in computer labs; classrooms have none.\n\n"
            + _table(["Room", "Name", "Type", "AC units"], [[r["room_id"], r["name"], r["type"], r["ac_units"]] for r in rows]))
    return _answer(text, INFRA_FILE, "Rooms sheet, AC Available column", INFRA_NOTE)


def cctv(block) -> list[dict]:
    """Counted from the detail sheets: the Summary sheet has no campus total and leaves out cameras in labs,
    lobbies, the library and other rooms, so adding up its rows would undercount."""
    sql, args = _filters(block, None, "e.")
    fsql, fargs = _filters(block, None, "f.")
    with _db() as con:
        rooms = con.execute(f"SELECT r.type, SUM(e.quantity) n FROM equipment e JOIN rooms r USING(room_id) "
                            f"WHERE e.item='CCTV camera'{sql} GROUP BY r.type ORDER BY n DESC", args).fetchall()
        outside = con.execute(f"SELECT f.name, SUM(f.quantity) n, SUM(f.operational_qty) ok FROM facilities f "
                              f"WHERE f.type='CCTV'{fsql} GROUP BY f.name ORDER BY n DESC", fargs).fetchall()
        broken = con.execute(f"SELECT e.room_id, e.quantity, e.operational_qty FROM equipment e WHERE e.item='CCTV camera' "
                             f"AND e.status!='Operational'{sql}", args).fetchall()
    lines = [[f"Inside rooms: {r['type']}", r["n"]] for r in rooms] + [[f["name"], f["n"]] for f in outside]
    total = sum(v for _, v in lines)
    where = _where(block, None)
    text = f"**{where[0].upper() + where[1:]} has {total} CCTV cameras.**\n\n" + _table(["Where", "Cameras"], lines)
    if broken:
        text += "\n\nNot fully working: " + "; ".join(f"{b['room_id']} ({b['operational_qty']} of {b['quantity']} working)"
                                                     for b in broken) + "."
    text += "\n\nCCTV covers corridors, lobbies, entrances, pathways, parking and open areas. Washrooms have no cameras."
    return _answer(text, INFRA_FILE, "Equipment and Facilities sheets, CCTV rows", INFRA_NOTE)


def not_working(block, floor) -> list[dict]:
    sql, args = _filters(block, floor)
    fsql, fargs = _filters(block, floor, "f.")
    with _db() as con:
        eq = con.execute(f"SELECT e.*, r.name room FROM equipment e JOIN rooms r USING(room_id) WHERE e.status!='Operational'"
                         f"{_filters(block, floor, 'e.')[0]} ORDER BY e.equipment_id", _filters(block, floor, 'e.')[1]).fetchall()
        fac = con.execute(f"SELECT * FROM facilities f WHERE f.status!='Operational'{fsql} ORDER BY facility_id", fargs).fetchall()
    where = _where(block, floor)
    rows = [[e["room_id"] + " " + e["room"], e["item"], f"{e['operational_qty']} of {e['quantity']}", e["status"]] for e in eq]
    rows += [[f"Block {f['block']}, floor {f['floor']} ({f['location']})", f["name"], f"{f['operational_qty']} of {f['quantity']}",
              f["status"]] for f in fac]
    if not rows:
        return _answer(f"**Everything in {where} is listed as operational.**", INFRA_FILE, "Equipment and Facilities sheets", INFRA_NOTE)
    text = (f"**In {where}, {len(eq)} equipment item{'' if len(eq) == 1 else 's'} and {len(fac)} facilit"
            f"{'y is' if len(fac) == 1 else 'ies are'} not fully working.**\n\n" + _table(["Where", "Item", "Working", "Status"], rows))
    return _answer(text, INFRA_FILE, "Equipment and Facilities sheets, Status column", INFRA_NOTE)


def facilities(ftype: str, block, floor) -> list[dict]:
    fsql, fargs = _filters(block, floor, "f.")
    with _db() as con:
        rows = con.execute(f"SELECT * FROM facilities f WHERE f.type=?{fsql} ORDER BY facility_id", [ftype, *fargs]).fetchall()
    where = _where(block, floor)
    if not rows:
        return _answer(f"**The dataset lists no {ftype.lower()} facilities in {where}.**", INFRA_FILE, "Facilities sheet", INFRA_NOTE)
    if ftype in ("Lift", "Staircase") and floor is None:  # the sheet repeats each lift once per floor it serves
        units = {}
        for r in rows:
            units.setdefault((r["block"], r["name"]), set()).add(r["status"])
        total = len(units)
        working = sum(st == {"Operational"} for st in units.values())
        head = (f"**{where[0].upper() + where[1:]} has {total} {ftype.lower()}{'' if total == 1 else 's'}, "
                f"{working} fully working.**")
        for (b, name), st in units.items():
            if st != {"Operational"}:
                head += f" {name} in Block {b} is {', '.join(sorted(st)).lower()}."
    else:
        total = sum(r["quantity"] for r in rows)
        working = sum(r["operational_qty"] for r in rows)
        head = (f"**{where[0].upper() + where[1:]} has {total} {ftype.lower()} unit{'' if total == 1 else 's'} "
                f"({working} working).**")
    text = (head + "\n\n"
            + _table(["Block", "Floor", "Name", "Location", "Qty", "Status", "Details"],
                     [[r["block"], r["floor"], r["name"], r["location"], r["quantity"], r["status"], r["details"]] for r in rows]))
    return _answer(text, INFRA_FILE, f"Facilities sheet, {ftype} rows", INFRA_NOTE)


def items(names: list[str], block, floor, rtype: str | None = None) -> list[dict]:
    marks = ",".join("?" * len(names))
    sql, args = _filters(block, floor, "e.")
    if rtype:
        sql += " AND r.type=?"
        args.append(rtype)
    with _db() as con:
        rows = con.execute(f"SELECT r.type, SUM(e.quantity) n, SUM(e.operational_qty) ok FROM equipment e JOIN rooms r "
                           f"USING(room_id) WHERE e.item IN ({marks}){sql} GROUP BY r.type ORDER BY n DESC",
                           [*names, *args]).fetchall()
    where = (f"{rtype.lower()}s in " if rtype else "") + _where(block, floor)
    total, ok = sum(r["n"] or 0 for r in rows), sum(r["ok"] or 0 for r in rows)
    label = " / ".join(names)
    text = (f"**{label}: {total} in {where}, {ok} of them working.**\n\n"
            + _table(["Room type", "Quantity", "Working"], [[r["type"], r["n"], r["ok"]] for r in rows]))
    return _answer(text, INFRA_FILE, f"Equipment sheet, {label}", INFRA_NOTE)


def find_room(text: str) -> list[dict]:
    """"Where is the library / exam cell / principal's office?" -> its room."""
    with _db() as con:
        rooms = con.execute("SELECT * FROM rooms").fetchall()
    low = text.lower()
    best = [r for r in rooms if r["name"].lower().replace("'s", "") in low.replace("'s", "")
            or (len(r["name"]) > 6 and re.sub(r"\s*\(.*\)", "", r["name"].lower()) in low)]
    if not best:
        return []
    return _answer("\n".join(f"**{r['name']}** is room {r['room_id']}, Block {r['block']}, {r['floor_name']}."
                             for r in best[:5]), INFRA_FILE, "Rooms sheet", INFRA_NOTE)


POLICY_RE = re.compile(r"\b(rules?|polic(y|ies)|fines?|penalt\w*|timings?|hours|open(ing)?|regulations?|allowed|"
                       r"permitted|borrow\w*|courses?|credits?|prerequisites?|grades?|cgpa|exams?|attendance)\b", re.I)
STRONG_RE = re.compile(r"\b(campus|infrastructure|blocks?|floors?|class ?rooms?|computer labs?|cctv|cameras?|washrooms?|"
                       r"toilets?|lifts?|elevators?|air[- ]?condition\w*|\bac\b|projectors?|equipment|facilit(y|ies)|"
                       r"seminar halls?|lobb(y|ies)|maintenance|not working|broken|seating|desktops?)\b", re.I)


def _kind(text: str):
    """What the text asks about, most specific first; None if it names nothing in the dataset."""
    if SUMMARY_RE.search(text) or re.fullmatch(r"\W*(campus|infrastructure)\W*", text, re.I):
        return ("summary", None)
    if BROKEN_RE.search(text):
        return ("broken", None)
    if re.search(r"\bcctv\b|\bcameras?\b", text, re.I):
        return ("cctv", None)
    if re.search(r"\bair[- ]?condition\w*\b|\bac\b", text, re.I):
        return ("ac", None)
    ftype = _match(text, FACILITY_TYPES)
    if ftype:
        return ("facility", ftype)
    item = _match(text, ITEMS)
    if item == ["Desktop computer"] and re.search(r"\bcomputer labs?\b", text, re.I) \
            and not re.search(r"\b(computers|desktops|pcs)\b", text, re.I):
        item = None  # "how many computer labs" asks about rooms
    if item:
        return ("item", (item, _match(re.sub(r"\bcomputers?\b", " ", text, flags=re.I), ROOM_TYPES)))
    rtype = _match(text, ROOM_TYPES)
    if rtype:
        return ("rooms", rtype)
    if re.search(r"\brooms?\b", text, re.I):
        return ("all_rooms", None)
    return None


def infra_route(q: str, message: str) -> list[dict]:
    now = message or q
    if POLICY_RE.search(now) and not STRONG_RE.search(now):
        return []  # "library rules", "lab courses": the handbook and course data answer these
    room = ROOM_ID_RE.search(now)
    if room:
        return room_detail(f"{room.group(1).upper()}-{room.group(2)}")
    block = _block(now) or (_block(q) if now != q else None)
    floor = _floor(now) if _floor(now) is not None else (_floor(q) if now != q else None)
    if re.search(r"\bwhere\b|\blocated\b|\blocation\b|\bwhich (room|block|floor)\b", now, re.I):
        found = find_room(now)
        if found:
            return found
        rtype = _match(now, ROOM_TYPES)
        if rtype:
            with _db() as con:
                rows = con.execute("SELECT * FROM rooms WHERE type=? ORDER BY room_id", (rtype,)).fetchall()
            if 0 < len(rows) <= 8:
                return _answer("\n".join(f"**{r['name']}** is room {r['room_id']}, Block {r['block']}, {r['floor_name']}."
                                         for r in rows), INFRA_FILE, f"Rooms sheet, {rtype} rows", INFRA_NOTE)
    kind = _kind(now) or (_kind(q) if now != q else None)  # "and in Block B?" keeps the earlier subject
    if kind is None:
        return rooms_by_block(block, floor) if (block or floor is not None) else []
    name, value = kind
    if name == "summary":
        return infra_summary()
    if name == "broken":
        return not_working(block, floor)
    if name == "cctv":
        return cctv(block)
    if name == "ac":
        return ac_rooms(block, floor)
    if name == "facility":
        return facilities(value, block, floor)
    if name == "item":
        return items(value[0], block, floor, value[1])
    if name == "rooms":
        dept = _match(now, DEPARTMENTS) if value in ("Faculty Office", "HOD Cabin") else None
        return rooms_of_type(value, block, floor, bool(COUNT_RE.search(now)), dept)
    return rooms_by_block(block, floor)


def rooms_by_block(block, floor) -> list[dict]:
    sql, args = _filters(block, floor)
    with _db() as con:
        rows = con.execute(f"SELECT type, COUNT(*) n, SUM(capacity) cap FROM rooms WHERE 1=1{sql} GROUP BY type ORDER BY n DESC",
                           args).fetchall()
    where = _where(block, floor)
    total = sum(r["n"] for r in rows)
    text = (f"**{where[0].upper() + where[1:]} has {total} rooms.**\n\n"
            + _table(["Room type", "Rooms", "Total capacity"], [[r["type"], r["n"], r["cap"]] for r in rows]))
    return _answer(text, INFRA_FILE, "Rooms sheet", INFRA_NOTE)


# ----------------------------------------------------------------------------------------------------------- BMS
def _sem(text: str) -> int | None:
    m = SEM_RE.search(text)
    if not m:
        return None
    v = (m.group(1) or m.group(2)).lower()
    return int(v) if v.isdigit() else ROMAN.get(v)


def bms_semester(sem: int) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM bms_courses WHERE semester=? ORDER BY source_row", (sem,)).fetchall()
    if not rows:
        text = (f"**The BMS sheet has no data for Semester {sem} yet.** Only Semesters 3, 5 and 7 are filled in, from "
                f"their timetables; Semesters 1, 2, 4, 6 and 8 are blank until those timetables are provided.")
        return _answer(text, BMS_FILE, "Sem_Spread_BMS, notes 1-2", BMS_NOTE)
    total = sum(r["credits"] for r in rows)
    text = (f"**BMS (Hons.) Digital Business, Semester {sem}: {len(rows)} courses, {_num(total)} credits in total** "
            f"(from the batch {rows[0]['timetable_batch']} timetable).\n\n"
            + _table(["Code", "Course", "Basket (inferred)", "L-T-P", "Credits"],
                     [[r["code"], r["title"], r["basket"], f"{r['L']}-{r['T']}-{r['P']}", r["credits"]] for r in rows]))
    if any(r["basket"] == "Minor / Open" for r in rows):
        text += ("\n\nThe Minor / Open rows are options for different minors, so a student does not take all of them.")
    return _answer(text, BMS_FILE, f"Sem_Spread_BMS, Semester {sem}", BMS_NOTE)


def bms_baskets() -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM bms_baskets ORDER BY source_row").fetchall()
    total = sum(r["min_credits"] for r in rows)
    text = (f"**BMS (Hons.) Digital Business needs {_num(total)} credits in total, across six baskets.**\n\n"
            + _table(["Basket", "Minimum credits", "Credits captured so far"],
                     [[r["basket"], r["min_credits"], r["captured_credits"]] for r in rows])
            + "\n\nThe minimums come from the official BMS programme page. \"Captured\" counts only courses in the "
              "Semester 3, 5 and 7 timetables, so it is incomplete; Minor / Open and Specialization exceed their "
              "minimums because those rows list options, not courses every student takes.")
    return _answer(text, BMS_FILE, "Sem_Spread_BMS, baskets and Fixed Min Credits", BMS_NOTE)


def bms_course(text: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM bms_courses").fetchall()
    low = text.lower()
    hits = [r for r in rows if re.search(rf"(?<![\w-]){re.escape(r['code'].lower())}(?![\w-])", low)
            or r["title"].lower().replace(" (r)", "") in low]
    if not hits:
        return []
    out = "\n".join(f"**{r['code']}: {r['title']}** is a BMS Semester {r['semester']} course in the {r['basket']} basket "
                    f"(inferred), L-T-P {r['L']}-{r['T']}-{r['P']}, {_num(r['credits'])} credits." for r in hits)
    return _answer(out, BMS_FILE, "Sem_Spread_BMS", BMS_NOTE)


def bms_route(q: str, message: str) -> list[dict]:
    now = message or q
    found = bms_course(now)
    if found:
        return found
    sem = _sem(now) or _sem(q)
    if sem:
        return bms_semester(sem)
    if re.search(r"\bbaskets?\b|\bcredits?\b|\bstructure\b|\brequire", now, re.I):
        return bms_baskets()
    if re.search(r"\b(courses?|subjects?|list|all|curriculum|syllabus)\b", now, re.I):
        return _bms_all()
    return bms_baskets()


def _bms_all() -> list[dict]:
    parts = [bms_semester(s)[0]["direct_answer"].replace("\n\n" + BMS_NOTE, "") for s in (3, 5, 7)]
    text = ("**The BMS sheet lists courses for Semesters 3, 5 and 7 only (40 courses); the other semesters are blank.**"
            "\n\n" + "\n\n".join(parts))
    return _answer(text, BMS_FILE, "Sem_Spread_BMS, Semesters 3, 5 and 7", BMS_NOTE)


def route(question: str, message: str | None = None) -> list[dict]:
    now = message or question
    if BMS_RE.search(now) or (BMS_RE.search(question) and not structured.SELF_CHECK_RE.search(now)
                              and not INFRA_RE.search(now)):
        return bms_route(question, message or question)
    if INFRA_RE.search(now) or ROOM_ID_RE.search(now) or (INFRA_RE.search(question) and (_block(now) or _floor(now) is not None)):
        return infra_route(question, message or question)
    return []
