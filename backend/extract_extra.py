"""
Adds the campus infrastructure file to the structured store (courses.db) and writes Markdown for retrieval.

    Infrastructure:  data/synthetic/college_infrastructure_synthetic.xlsx   (SYNTHETIC demo campus data)

The file describes itself as synthetic, so every answer built from it says so. (The draft BMS semester spread is no
longer used; its tables are dropped.) Nothing is inferred here: values are copied cell by cell, and the summary sheet is checked against the
detail sheets (python extract_extra.py fails if any figure disagrees).

Run after extract_data.py, then ingest.py:  python extract_extra.py
"""
import sqlite3

import openpyxl

from config import COURSES_DB, PROCESSED_DIR, SYNTHETIC_DIR

INFRA_XLSX = SYNTHETIC_DIR / "college_infrastructure_synthetic.xlsx"
INFRA_FILE = "College_Infrastructure_Dataset_SYNTHETIC.xlsx"
# Semester -> first column (Code); then Course, L, T, P, C. Taken from row 4 of the sheet.
# Which admission batch each populated semester's timetable came from (sheet note 1).


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
    """Campus tables only. The draft BMS spread is not used any more (its tables are dropped if present)."""
    infra = parse_infra()
    check_summary(infra)
    db = sqlite3.connect(db_path)
    db.executescript("""
    DROP TABLE IF EXISTS bms_courses; DROP TABLE IF EXISTS bms_baskets; DROP TABLE IF EXISTS rooms;
    DROP TABLE IF EXISTS equipment; DROP TABLE IF EXISTS facilities; DROP TABLE IF EXISTS infra_summary;
    DROP TABLE IF EXISTS infra_rules;
    CREATE TABLE rooms (room_id TEXT PRIMARY KEY, block TEXT, floor INTEGER, floor_name TEXT, name TEXT, type TEXT,
        department TEXT, capacity INTEGER, ac TEXT, ac_units INTEGER, source_row INTEGER);
    CREATE TABLE equipment (equipment_id TEXT PRIMARY KEY, room_id TEXT, block TEXT, floor INTEGER, item TEXT,
        category TEXT, quantity INTEGER, operational_qty INTEGER, status TEXT, source_row INTEGER);
    CREATE TABLE facilities (facility_id TEXT PRIMARY KEY, block TEXT, floor TEXT, type TEXT, name TEXT, location TEXT,
        quantity INTEGER, operational_qty INTEGER, status TEXT, details TEXT, source_row INTEGER);
    CREATE TABLE infra_summary (metric TEXT, block_a REAL, block_b REAL, block_c REAL, total REAL, source_row INTEGER);
    CREATE TABLE infra_rules (section TEXT, text TEXT, source_row INTEGER);
    """)
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
    return infra


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
    infra = build()
    (PROCESSED_DIR / "bms_structure.md").unlink(missing_ok=True)
    (PROCESSED_DIR / "campus_infrastructure.md").write_text(infra_md(infra))
    print(f"Infrastructure: {len(infra['rooms'])} rooms, {len(infra['equipment'])} equipment rows, "
          f"{len(infra['facilities'])} facilities; summary sheet matches the detail sheets")


if __name__ == "__main__":
    main()
