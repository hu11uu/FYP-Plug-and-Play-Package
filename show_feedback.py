"""Show the survey answers saved for one subject.

Each subject keeps its saved surveys in runtime/<subject>.sqlite. This script
prints them in a readable form, newest first, with the average rating.

    python3 show_feedback.py statistics
    python3 show_feedback.py marketing --database path/to/other.sqlite
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("subject", help="subject folder name, e.g. statistics")
parser.add_argument("--database", help="a database file other than runtime/<subject>.sqlite")
args = parser.parse_args()

path = Path(args.database) if args.database else ROOT / "runtime" / f"{args.subject}.sqlite"
if not path.exists():
    raise SystemExit(f"No saved feedback yet: {path} does not exist. Run a host for '{args.subject}' and answer a survey first.")

with sqlite3.connect(path) as db:
    rows = db.execute("SELECT body FROM documents WHERE collection = 'chatbot.surveys'").fetchall()
surveys = sorted((json.loads(body) for (body,) in rows), key=lambda s: s.get("timestamp", ""), reverse=True)

if not surveys:
    raise SystemExit(f"No surveys saved yet for '{args.subject}'.")

ratings = [a for s in surveys for a in s.get("answers", [])]
print(f"{len(surveys)} survey(s) saved for '{args.subject}', {len(ratings)} ratings, "
      f"average {sum(ratings) / len(ratings):.1f} out of 5\n")

for s in surveys:
    print(f"{s.get('timestamp', '?')}  (after: {s.get('last_query') or '-'})")
    for question, answer in zip(s.get("questions", []), s.get("answers", [])):
        print(f"  {answer}/5  {question}")
    print()
