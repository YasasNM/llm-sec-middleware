# Test data for C3

Everything here is written by hand for testing and is synthetic. None of it is real patient data or a real hospital policy.

## 1. Answers to check (`cases.csv`)

38 cases for the **Riverside AI policy** that the demo loads: 20 answers that break a rule (`V01` to `V20`) and 18 that are safe (`C01` to `C18`).
Columns: `id, expected (withheld or clean), rule (the clause it should break), level (easy, paraphrase, hard, injection), prompt, answer, note`.

Run them all against the running demo (start it with `start_demo.bat` first):

```
.venv\Scripts\python tools\run_testdata.py
```

It prints one line per case and a summary, and saves details to `var/testdata_results.json`.
Add `--all-policies` to search every loaded policy instead of only Riverside.

To try one by hand, copy the prompt and answer into the Chat tab and switch to Admin.

**Result on the current model (demo with Riverside only):**

| Measure | Result |
|---|---|
| Correct verdicts | 28 of 38 (74%) |
| Violations caught | 19 of 20 |
| False alarms on safe answers | 9 of 18 |
| Right rule cited when caught | 13 of 19 |

The model catches almost every violation but also flags safe answers that mention the same topic, for example "Not without her consent" is flagged under rule 6.1. Quote these numbers as they are.

## 2. Policy files (`policies/`)

Regenerate with `.venv\Scripts\python tools\make_testdata.py`.

| File | Use it to test | Expected |
|---|---|---|
| `clinic-conduct-policy.md` and `.pdf` | FR1 parsing: numbered clauses, headers and page numbers removed in the PDF | 10 clauses with stable IDs |
| `clinic-conduct-policy-v2.md` and `.pdf` | FR2: use Replace on the first file | 1 added (4.2), 1 changed (4), 1 removed (9), 8 unchanged; only 2 clauses re-embedded |
| `telehealth-policy.md` | A second policy next to the first (several documents, one index) | 5 clauses |
| `unnumbered-uppercase-titles.pdf` | FR1: no numbers, IDs come from the titles | `DATA HANDLING`, `PRESCRIBING`, `INCIDENT REPORTING` become 3 clauses |
| `bad-scanned-no-text.pdf` | Rejection: no text layer | Rejected, "No text layer found" |
| `bad-not-a-pdf.pdf` | Rejection: wrong file content | Rejected, "The file is not a PDF" |
| `bad-empty.md` | Rejection: no clauses | Rejected, "No clause headings found" |
| `bad-wrong-type.txt` | Rejection: wrong file type | Rejected, "Only PDF and Markdown files are accepted" |

Also available in the demo's Policies tab: the real Sri Lankan Data Protection Act (PDF) and the HIPAA Privacy Rule.

## 3. A short FR2 walk-through

1. Policies tab: upload `clinic-conduct-policy.pdf`. Open Clauses and note the IDs.
2. Press Replace on that row and choose `clinic-conduct-policy-v2.pdf`. Open Clauses again: IDs for unchanged clauses are the same, 4.2 is new and clause 9 is gone.
3. The message under the table says how quickly the new version went live. For the exact counts (1 added, 1 changed, 1 removed, 2 re-embedded) open http://127.0.0.1:8000/api/admin/runs?limit=1 in a browser.
