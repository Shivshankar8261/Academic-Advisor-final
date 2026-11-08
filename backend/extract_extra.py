"""
Adds the two November 2026 files to the structured store (courses.db) and writes Markdown for retrieval.

    BMS spread:      data/raw/bms_semester_spread_sept2026.xlsx   (B.M.S. (Hons.) Digital Business, a DRAFT)
    Infrastructure:  data/synthetic/college_infrastructure_synthetic.xlsx   (SYNTHETIC demo campus data)

Both files describe themselves as not official, so every row keeps that status and every answer built from them
says so. Nothing is inferred here: values are copied cell by cell, and the summary sheet is checked against the
detail sheets (python extract_extra.py fails if any figure disagrees).

Run after extract_data.py, then ingest.py:  python extract_extra.py
"""
import re
import sqlite3

import openpyxl

from config import COURSES_DB, PROCESSED_DIR, RAW_DIR, SYNTHETIC_DIR

BMS_XLSX = RAW_DIR / "bms_semester_spread_sept2026.xlsx"
INFRA_XLSX = SYNTHETIC_DIR / "college_infrastructure_synthetic.xlsx"
BMS_FILE = "Semester_Spread_Structures_Sept_2026_with_BMS.xlsx"     # names cited to students
INFRA_FILE = "College_Infrastructure_Dataset_SYNTHETIC.xlsx"
BMS_PROGRAMME = "B.M.S. (Hons.) Digital Business"
# Semester -> first column (Code); then Course, L, T, P, C. Taken from row 4 of the sheet.
BMS_SEM_COLS = {1: "D", 2: "K", 3: "R", 4: "Y", 5: "AF", 6: "AM", 7: "AT", 8: "BA"}
# Which admission batch each populated semester's timetable came from (sheet note 1).
BMS_SEM_BATCH = {3: "2025", 5: "2023", 7: "2023"}


def _col(letter: str, offset: int = 0) -> int:
    return openpyxl.utils.column_index_from_string(letter) + offset


def parse_bms():
    ws = openpyxl.load_workbook(BMS_XLSX, data_only=True).worksheets[0]
    title, sources = ws["A1"].value, ws["A2"].value
    notes = [ws.cell(r, 1).value for r in range(31, 37) if ws.cell(r, 1).value]
    baskets, courses, basket = [], [], None
    for r in range(5, 28):
        if ws.cell(r, 1).value:  # a basket starts on this row (merged down over its course rows)
            basket = ws.cell(r, 1).value.strip()
            baskets.append({"basket": basket, "min_credits": ws.cell(r, 2).value,
                            "captured_credits": ws.cell(r, _col("BH")).value, "source_row": r})
        for sem, letter in BMS_SEM_COLS.items():
            code = ws.cell(r, _col(letter)).value
            if not code:
                continue
            l, t, p, c = (ws.cell(r, _col(letter, k)).value for k in range(2, 6))
            courses.append({"semester": sem, "basket": basket, "code": str(code).strip(),
                            "title": str(ws.cell(r, _col(letter, 1)).value).strip(), "L": l, "T": t, "P": p,
                            "credits": c, "timetable_batch": BMS_SEM_BATCH.get(sem), "source_row": r})
    total = {"min_credits": ws["B28"].value, "captured_credits": ws["BH28"].value}
    sems_with_data = sorted({c["semester"] for c in courses})
    assert sems_with_data == [3, 5, 7], sems_with_data
    for b in baskets:  # the sheet's own formula total must match the rows we read
        assert sum(c["credits"] for c in courses if c["basket"] == b["basket"]) == b["captured_credits"], b
    return {"title": title, "sources": sources, "notes": notes, "baskets": baskets, "courses": courses, "total": total}


def _table(wb, name):
    rows = list(wb[name].iter_rows(values_only=True))
    header = rows[2]  # rows 1-2: the "SYNTHETIC/DEMO DATA" banner and a blank row
    return [dict(zip(header, r), source_row=i + 4) for i, r in enumerate(rows[3:]) if r[0]]


def parse_infra():
    wb = openpyxl.load_workbook(INFRA_XLSX, data_only=True)
    summary_rows = [r for r in wb["Summary"].iter_rows(values_only=True)]
    metrics, rules, section = [], [], None
    for i, r in enumerate(summary_rows, 1):
        if not r[0]:
            continue
        if r[0] in ("Infrastructure rules", "Layout notes"):
            section = r[0]
        elif section:
            rules.append({"section": section, "text": r[0], "source_row": i})
        elif r[0] != "Metric" and any(v is not None for v in r[1:5]):
            metrics.append({"metric": r[0], "A": r[1], "B": r[2], "C": r[3], "total": r[4], "source_row": i})
    return {"metrics": metrics, "rules": rules, "rooms": _table(wb, "Rooms"),
            "equipment": _table(wb, "Equipment"), "facilities": _table(wb, "Facilities")}


def check_summary(infra):
    """Recompute every block metric from the detail sheets; the file must agree with itself."""
    rooms, eq, fac = infra["rooms"], infra["equipment"], infra["facilities"]
    of_type = lambda b, t: [r for r in rooms if r["Block"] == b and r["Type"] == t]  # noqa: E731
    ids = lambda rs: {r["Room ID"] for r in rs}  # noqa: E731
    qty = lambda b, item, rs: sum(e["Quantity"] for e in eq if e["Block"] == b and e["Item"] == item  # noqa: E731
                                  and e["Room ID"] in ids(rs))
    calc = {
        "Classrooms": lambda b: len(of_type(b, "Classroom")),
        "Classroom seating capacity": lambda b: sum(r["Capacity"] for r in of_type(b, "Classroom")),
        "Computer labs": lambda b: len(of_type(b, "Computer Lab")),
        "Desktop computers in labs": lambda b: qty(b, "Desktop computer", of_type(b, "Computer Lab")),
        "Faculty offices (3rd floor)": lambda b: len(of_type(b, "Faculty Office")),
        "Lobbies (ground floor)": lambda b: len(of_type(b, "Lobby")),
        "Total rooms": lambda b: sum(r["Block"] == b for r in rooms),
        "CCTV cameras in classrooms (2 per classroom)": lambda b: qty(b, "CCTV camera", of_type(b, "Classroom")),
        "Interactive touch-screen displays in classrooms (1 per classroom)":
            lambda b: qty(b, "Interactive touch-screen display", of_type(b, "Classroom")),
        "Corridor and entrance CCTV cameras": lambda b: sum(f["Quantity"] for f in fac if f["Block"] == b and f["Type"] == "CCTV"),
        "Rooms with AC": lambda b: sum(r["Block"] == b and r["AC Available"] == "Yes" for r in rooms),
        "Equipment items not fully operational": lambda b: sum(e["Block"] == b and e["Status"] != "Operational" for e in eq),
    }
    for m in infra["metrics"]:
        if m["metric"] in calc:
            got = [calc[m["metric"]](b) for b in "ABC"]
            assert got == [m["A"], m["B"], m["C"]] and sum(got) == m["total"], (m, got)
        else:
            assert m["metric"].startswith("Campus-wide CCTV"), m
            got = sum(f["Quantity"] for f in fac if f["Block"] == "Campus" and f["Type"] == "CCTV")
            assert got == m["total"], (m, got)


def build(db_path=COURSES_DB):
    bms, infra = parse_bms(), parse_infra()
    check_summary(infra)
    db = sqlite3.connect(db_path)
    db.executescript("""
    DROP TABLE IF EXISTS bms_courses; DROP TABLE IF EXISTS bms_baskets; DROP TABLE IF EXISTS rooms;
    DROP TABLE IF EXISTS equipment; DROP TABLE IF EXISTS facilities; DROP TABLE IF EXISTS infra_summary;
    DROP TABLE IF EXISTS infra_rules;
    CREATE TABLE bms_courses (semester INTEGER, basket TEXT, code TEXT, title TEXT, L INTEGER, T INTEGER, P INTEGER,
        credits REAL, timetable_batch TEXT, source_row INTEGER);
    CREATE TABLE bms_baskets (basket TEXT, min_credits REAL, captured_credits REAL, source_row INTEGER);
    CREATE TABLE rooms (room_id TEXT PRIMARY KEY, block TEXT, floor INTEGER, floor_name TEXT, name TEXT, type TEXT,
        department TEXT, capacity INTEGER, ac TEXT, ac_units INTEGER, source_row INTEGER);
    CREATE TABLE equipment (equipment_id TEXT PRIMARY KEY, room_id TEXT, block TEXT, floor INTEGER, item TEXT,
        category TEXT, quantity INTEGER, operational_qty INTEGER, status TEXT, source_row INTEGER);
    CREATE TABLE facilities (facility_id TEXT PRIMARY KEY, block TEXT, floor TEXT, type TEXT, name TEXT, location TEXT,
        quantity INTEGER, operational_qty INTEGER, status TEXT, details TEXT, source_row INTEGER);
    CREATE TABLE infra_summary (metric TEXT, block_a REAL, block_b REAL, block_c REAL, total REAL, source_row INTEGER);
    CREATE TABLE infra_rules (section TEXT, text TEXT, source_row INTEGER);
    """)
    db.executemany("INSERT INTO bms_courses VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(c["semester"], c["basket"], c["code"], c["title"], c["L"], c["T"], c["P"], c["credits"],
                     c["timetable_batch"], c["source_row"]) for c in bms["courses"]])
    db.executemany("INSERT INTO bms_baskets VALUES (?,?,?,?)",
                   [(b["basket"], b["min_credits"], b["captured_credits"], b["source_row"]) for b in bms["baskets"]])
    db.executemany("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   [(r["Room ID"], r["Block"], r["Floor"], r["Floor Name"], r["Name"], r["Type"], r["Department"],
                     r["Capacity"], r["AC Available"], r["AC Units"], r["source_row"]) for r in infra["rooms"]])
    db.executemany("INSERT INTO equipment VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(e["Equipment ID"], e["Room ID"], e["Block"], e["Floor"], e["Item"], e["Category"], e["Quantity"],
                     e["Operational Qty"], e["Status"], e["source_row"]) for e in infra["equipment"]])
    db.executemany("INSERT INTO facilities VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   [(f["Facility ID"], f["Block"], str(f["Floor"]), f["Type"], f["Name"], f["Location"], f["Quantity"],
                     f["Operational Qty"], f["Status"], f["Details"], f["source_row"]) for f in infra["facilities"]])
    db.executemany("INSERT INTO infra_summary VALUES (?,?,?,?,?,?)",
                   [(m["metric"], m["A"], m["B"], m["C"], m["total"], m["source_row"]) for m in infra["metrics"]])
    db.executemany("INSERT INTO infra_rules VALUES (?,?,?)", [(x["section"], x["text"], x["source_row"]) for x in infra["rules"]])
    db.commit()
    db.close()
    return bms, infra


def _n(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def bms_md(bms) -> str:
    out = [f"# {BMS_PROGRAMME}: semester spread (DRAFT, source: {BMS_FILE})", "",
           "## Status of this document",
           "This sheet is a DRAFT, not an official University document. " + bms["sources"], ""]
    out += [f"- {n}" for n in bms["notes"]]
    out += ["", "## Credit baskets (minimum credits from the official BMS programme page)", ""]
    out += [f"- {b['basket']}: minimum {_n(b['min_credits'])} credits; {_n(b['captured_credits'])} credits captured "
            f"from the available timetables" for b in bms["baskets"]]
    out += [f"- Total: minimum {_n(bms['total']['min_credits'])} credits; {_n(bms['total']['captured_credits'])} captured", ""]
    for sem in range(1, 9):
        rows = [c for c in bms["courses"] if c["semester"] == sem]
        out.append(f"## BMS Semester {sem}")
        if not rows:
            out += ["No timetable data has been provided for this semester; the sheet leaves it blank.", ""]
            continue
        out.append(f"Taken from the batch {rows[0]['timetable_batch']} timetable. Basket placement is inferred, not official.")
        out += [f"- {c['code']}: {c['title']} ({c['basket']}), L-T-P {c['L']}-{c['T']}-{c['P']}, {_n(c['credits'])} credits"
                for c in rows]
        out.append("")
    return "\n".join(out)


def infra_md(infra) -> str:
    out = [f"# Campus infrastructure (SYNTHETIC demo data, source: {INFRA_FILE})", "",
           "## Status of this data",
           "This is SYNTHETIC sample campus data created for the AI Academic Advisor demo. It does not describe the "
           "real Vidyashilp University campus.", "", "## Block-wise summary", ""]
    out += [f"- {m['metric']}: Block A {m['A']}, Block B {m['B']}, Block C {m['C']}, campus total {m['total']}"
            if m["A"] is not None else f"- {m['metric']}: campus total {m['total']}" for m in infra["metrics"]]
    for section in ("Infrastructure rules", "Layout notes"):
        out += ["", f"## {section}", ""] + [f"- {x['text']}" for x in infra["rules"] if x["section"] == section]
    for block in "ABC":
        for floor in range(4):
            rows = [r for r in infra["rooms"] if r["Block"] == block and r["Floor"] == floor]
            out += ["", f"## Block {block}, {rows[0]['Floor Name']} rooms", ""]
            out += [f"- {r['Room ID']}: {r['Name']} ({r['Type']}{', ' + r['Department'] if r['Department'] else ''}), "
                    f"capacity {r['Capacity']}, AC {r['AC Available']}" for r in rows]
    out += ["", "## Equipment not fully operational", ""]
    out += [f"- {e['Equipment ID']} in {e['Room ID']}: {e['Item']}, {e['Operational Qty']} of {e['Quantity']} working "
            f"({e['Status']})" for e in infra["equipment"] if e["Status"] != "Operational"]
    out += ["", "## Facilities not fully operational", ""]
    out += [f"- {f['Facility ID']}: {f['Name']}, Block {f['Block']}, floor {f['Floor']}, {f['Location']} ({f['Status']})"
            for f in infra["facilities"] if f["Status"] != "Operational"]
    return "\n".join(out) + "\n"


def main():
    bms, infra = build()
    (PROCESSED_DIR / "bms_structure.md").write_text(bms_md(bms))
    (PROCESSED_DIR / "campus_infrastructure.md").write_text(infra_md(infra))
    print(f"BMS: {len(bms['courses'])} courses in semesters 3, 5, 7; {len(bms['baskets'])} baskets")
    print(f"Infrastructure: {len(infra['rooms'])} rooms, {len(infra['equipment'])} equipment rows, "
          f"{len(infra['facilities'])} facilities; summary sheet matches the detail sheets")


if __name__ == "__main__":
    main()
