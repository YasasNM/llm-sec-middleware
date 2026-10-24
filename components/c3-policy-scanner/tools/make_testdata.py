# ruff: noqa: E501
"""Write the policy files in testdata/policies for trying FR1 (parsing) and FR2 (live re-indexing).

    .venv\\Scripts\\python tools\\make_testdata.py

testdata/cases.csv (answers with expected verdicts) is written by hand and is not touched here.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from c3_helpers import make_pdf, make_scanned_pdf, make_uppercase_pdf  # noqa: E402

OUT = ROOT / "testdata" / "policies"

CONDUCT = [
    ("1", "Purpose", "This policy sets out how clinic staff may use the AI assistant in patient care."),
    ("2", "Scope", "It applies to every doctor, nurse and administrator who uses the assistant."),
    ("3", "Patient identity", "The assistant must not output a patient's name, address, phone number or national identity number."),
    ("4", "Medicines", "The assistant must not tell a patient to start, stop or change a medicine. Only the prescriber may do that."),
    ("4.1", "Controlled drugs", "The assistant must not give dosing instructions for opioids or sedatives."),
    ("5", "Urgent care", "The assistant must not delay urgent care when a patient describes chest pain, stroke signs or heavy bleeding."),
    ("6", "Sharing information", "Patient information must not be shared with relatives, employers or insurers without the patient's consent."),
    ("7", "Diagnosis", "The assistant must not state a diagnosis as certain. A clinician confirms every diagnosis."),
    ("8", "Incidents", "A suspected data breach must be reported to the privacy officer within 24 hours."),
    ("9", "Review", "This policy is reviewed every year by the governance committee."),
]

# v2 of the same policy: clause 4 reworded (changed), 4.2 added (new), clause 9 removed.
CONDUCT_V2 = [c for c in CONDUCT if c[0] != "9"]
CONDUCT_V2 = [
    (n, t, "The assistant must not tell a patient to start, stop or change any medicine, including over-the-counter products. Only the prescriber may do that." if n == "4" else b)
    for n, t, b in CONDUCT_V2
]
CONDUCT_V2.insert(5, ("4.2", "Antibiotics", "The assistant must not recommend an antibiotic course without a clinician's review."))

TELEHEALTH = [
    ("1", "Consent", "A telehealth visit may only begin after the patient has agreed to a remote consultation."),
    ("2", "Recording", "A remote consultation must not be recorded unless the patient agrees in writing."),
    ("3", "Prescribing", "A clinician must not prescribe a controlled drug in a first remote consultation."),
    ("4", "Identity", "The clinician must confirm the patient's identity before sharing any result."),
    ("5", "Emergencies", "When a patient describes an emergency, the clinician must direct them to emergency services."),
]

def md(clauses) -> str:
    out = ["# Clinic Conduct Policy", ""]
    for num, title, body in clauses:
        out += [f"## {num} {title}", "", body, ""]
    return "\n".join(out)


def write(name: str, data: bytes | str) -> None:
    path = OUT / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))   # bytes: keeps hashes stable
    print("wrote", path.relative_to(ROOT))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("clinic-conduct-policy.md", md(CONDUCT))
    write("clinic-conduct-policy-v2.md", md(CONDUCT_V2))
    write("telehealth-policy.md", md(TELEHEALTH))
    write("unnumbered-uppercase-titles.pdf", make_uppercase_pdf())      # no numbers: clauses get ids made from their titles
    write("clinic-conduct-policy.pdf", make_pdf(CONDUCT, header="Riverside Clinic - Conduct Policy", per_page=4))
    write("clinic-conduct-policy-v2.pdf", make_pdf(CONDUCT_V2, header="Riverside Clinic - Conduct Policy", per_page=4))
    # files that must be rejected
    write("bad-scanned-no-text.pdf", make_scanned_pdf())
    write("bad-not-a-pdf.pdf", "This is plain text with a .pdf name.")
    write("bad-empty.md", "")
    write("bad-wrong-type.txt", "Only PDF and Markdown files are accepted.")


if __name__ == "__main__":
    main()
