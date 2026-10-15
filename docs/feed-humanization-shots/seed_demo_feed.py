#!/usr/bin/env python3
"""
Seed a realistic demo feed through the REAL emit paths.

Nothing here writes events directly. Emails go through
email-router/scripts/classify_email.py::emit_feed_event and tasks go through the
add_task.py / complete_task.py CLIs, so what lands on the feed is exactly what
production code produces — including the headline/detail schema and the copy
rules. If a rule regresses, this seed shows it.

$JARVIS_DEMO_SEED is set by the caller: notify.build_event() suffixes every
resulting `detail` with "demo-seeded", so a seeded event can always be told from
a real one.

Fixture identities are deliberately NEW external parties rather than the real
people already in this tenant's feed — a demo should not put fabricated emails
in a real colleague's name. Subjects intentionally carry the messy patterns the
humanisation pass exists to clean up (Re:, [bracket-ids], '@').
"""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

ROOT = Path('/root/.hermes/skills/openclaw-imports')
DB = ROOT / 'task-manager' / 'data' / 'tasks.db'
OWNER = 'spencerhuck@34dev.com'

sys.path.insert(0, str(ROOT / '_shared'))
sys.path.insert(0, str(ROOT / 'email-router' / 'scripts'))
import classify_email as ce  # noqa: E402

env = dict(os.environ, JARVIS_DEMO_SEED='1')


def email(category, confidence, sender, subject, reasoning):
    ce.emit_feed_event(
        {'classification': category, 'confidence': confidence, 'reasoning': reasoning},
        sender, subject, message_id='<demo-%s@seed.local>' % abs(hash(subject)))
    print(f'  email  {category}/{confidence:<6} {subject[:58]}')


def add_task(title, priority):
    r = subprocess.run(
        [sys.executable, str(ROOT / 'task-manager/scripts/add_task.py'),
         '--title', title, '--assignee', OWNER, '--priority', priority,
         '--source', 'email', '--actor', OWNER],
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f'add_task failed: {r.stderr.strip()}')
    tid = sqlite3.connect(DB).execute(
        'select task_id from tasks order by rowid desc limit 1').fetchone()[0]
    print(f'  task+  {tid}  {title[:52]}')
    return tid


def complete(tid):
    r = subprocess.run(
        [sys.executable, str(ROOT / 'task-manager/scripts/complete_task.py'),
         '--task-id', tid, '--actor', OWNER],
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f'complete_task failed: {r.stderr.strip()}')
    print(f'  task✓  {tid}')


# Emitted oldest-first so the feed reads as a plausible morning of work.
print('seeding 8 demo events through the real emit paths:')

# A buyer lead comes in, and the follow-up it generates.
email('G', 'medium', 'Marcus Webb <marcus.webb@gmail.example>',
      'Inquiry from HAR.com - 4211 Oak Forest Dr',
      'Prospective buyer asking about availability and a showing window.')
lead_task = add_task('Call Marcus Webb about the Oak Forest showing', 'high')

# The urgent insurance thread — the archetype the copy rules were written for.
email('A', 'high', 'Renee Alvarado <renee@gulfcoast-insure.example>',
      "Re: [POL-88213] Builder's Risk - 3308 Rusk St - PENDING CANCELLATION",
      'Carrier requires proof of coverage before Friday or the policy lapses.')
add_task("Send the updated Builder's Risk binder to the lender", 'high')

# A decision only the owner can make.
email('C', 'high', 'Danielle Pham <dpham@lonestar-title.example>',
      'Survey discrepancy at 609 E 25th - need seller decision',
      'Title company flagged an encroachment; requires an owner decision on credit.')

# Something for the calendar.
email('B', 'high', 'Priya Raman <priya.raman@stewart-title.example>',
      'Closing @ 2:30 Thu Aug 20 - 340 W 27th St',
      'Closing time confirmed by all parties; needs calendaring only.')

# The follow-up gets done.
complete(lead_task)

# Pure information, filed and out of the way.
email('D', 'medium', 'reports@har-listings.example',
      'Weekly Traffic Report - 609 + 611B E 25th St',
      'Automated listing traffic summary. No response required.')

print('\ndone.')
