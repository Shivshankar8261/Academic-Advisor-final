"""Campus infrastructure answers, checked against the spreadsheets themselves (not against our code).
Offline, no model: python -m pytest eval/test_campus.py -q"""
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import advisor  # noqa: E402
import campus  # noqa: E402
import llm  # noqa: E402
import memory  # noqa: E402
import structured  # noqa: E402

WB = openpyxl.load_workbook(ROOT / "data/synthetic/college_infrastructure_synthetic.xlsx", data_only=True)


def sheet(name):
    rows = list(WB[name].iter_rows(values_only=True))
    return [dict(zip(rows[2], r)) for r in rows[3:] if r[0]]


ROOMS, EQ, FAC = sheet("Rooms"), sheet("Equipment"), sheet("Facilities")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("an exact question must not call a model")
    monkeypatch.setattr(llm, "call_structured", fail)
    monkeypatch.setattr(llm, "call_text", fail)


def ask(text, history=None):
    return advisor.answer(text, "rag_structured", None, history or [], guarded=True, use_classifier=False)


def first_line(text):
    return structured.route(text, None, text)[0]["direct_answer"].splitlines()[0]


@pytest.mark.parametrize("block", ["A", "B", "C"])
def test_classrooms_and_seats_per_block(block):
    cls = [r for r in ROOMS if r["Block"] == block and r["Type"] == "Classroom"]
    line = first_line(f"How many classrooms are in Block {block}?")
    assert f"{len(cls)} classrooms" in line and str(sum(r["Capacity"] for r in cls)) in line


def test_cctv_total_counts_every_camera_not_only_the_summary_rows():
    total = sum(e["Quantity"] for e in EQ if e["Item"] == "CCTV camera") + sum(f["Quantity"] for f in FAC if f["Type"] == "CCTV")
    assert total == 191 and "191 CCTV cameras" in first_line("How many CCTV cameras are on campus?")


def test_lab_desktops_match_the_summary_sheet():
    labs = {r["Room ID"] for r in ROOMS if r["Type"] == "Computer Lab"}
    n = sum(e["Quantity"] for e in EQ if e["Room ID"] in labs and e["Item"] == "Desktop computer")
    assert n == 212 and "212 in computer labs" in first_line("How many computers are in the labs?")


def test_lifts_are_counted_once_not_once_per_floor():
    line = first_line("Is the lift working in Block A?")
    assert "2 lifts, 1 fully working" in line and "Lift L2 in Block A is under maintenance" in line


def test_everything_not_working_is_listed():
    answer = structured.route("What equipment is not working?")[0]["direct_answer"]
    broken = [e["Equipment ID"] for e in EQ if e["Status"] != "Operational"]
    assert f"{len(broken)} equipment items" in answer
    for e in EQ:
        if e["Status"] != "Operational":
            assert e["Room ID"] in answer


def test_ac_rooms_are_exactly_the_yes_rows():
    answer = structured.route("Which rooms have AC?")[0]["direct_answer"]
    yes = [r["Room ID"] for r in ROOMS if r["AC Available"] == "Yes"]
    assert f"{len(yes)} rooms" in answer and all(rid in answer for rid in yes)


@pytest.mark.parametrize("question, room", [("Where is the library?", "B-002"), ("Where is the examination cell?", "C-004"),
                                            ("Where is the principal's office?", "A-004"), ("tell me about B-105", "B-105")])
def test_room_locations(question, room):
    assert room in structured.route(question)[0]["direct_answer"]


def test_room_detail_lists_its_equipment():
    answer = structured.route("What is in room A-207?")[0]["direct_answer"]
    for e in EQ:
        if e["Room ID"] == "A-207":
            assert e["Item"] in answer


def test_department_is_not_mistaken_for_computers():
    assert "faculty offices" in first_line("How many faculty offices does Computer Science have?")


def test_draft_bms_spread_is_not_used():
    """The draft BMS spread was withdrawn: BMS questions must reach normal search, never an exact-answer shortcut."""
    for q in ["What BMS courses are in semester 3?", "How many credits does the BMS programme need?",
              "who all are the bms faculties ??"]:
        assert campus.route(q) == [] and structured.route(q) == []


@pytest.mark.parametrize("question", ["How many classrooms are there?", "Which rooms have AC?", "Where is the library?"])
def test_campus_answers_say_the_data_is_synthetic(question):
    assert "synthetic demo dataset" in ask(question).answer


def test_follow_up_keeps_the_campus_subject():
    history = [{"role": "user", "content": "How many classrooms are in Block A?"}, {"role": "assistant", "content": "12"}]
    assert "Block B has 12 classrooms" in ask("what about Block B?", history).answer


@pytest.mark.parametrize("question", ["What are the library rules?", "Which courses have lab components?",
                                      "What is the minimum attendance?", "Can I take Machine Learning next semester?"])
def test_academic_questions_are_not_taken_over(question):
    chunks = campus.route(question)
    assert chunks == []


def test_btech_answers_unchanged():
    assert "14 courses" in first_line("How many courses are in the Foundation basket for the 2022 batch?")


@pytest.mark.parametrize("text", ["BMS semester 2", "bms sem 3", "BMS Semester III"])
def test_bms_semester_alone_is_a_question_not_a_fact_about_the_student(text):
    assert memory.recall_kind(text, memory.Facts(), []) != "noted"
