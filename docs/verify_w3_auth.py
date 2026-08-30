"""W3 half 1: per-person auth + roles, enforced by the API."""
import json, subprocess, sys, pathlib
PINS = json.loads(pathlib.Path('/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/pins.json').read_text())
BASE = 'http://127.0.0.1:8700'
ok = []
def ck(n, c, x=''):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else "")); ok.append(c)

def call(path, method='GET', body=None, jar=None):
    cmd = ['curl','-s','-o','/tmp/w3body','-w','%{http_code}','-X',method, BASE+path]
    if jar: cmd += ['-b', jar, '-c', jar]
    if body is not None:
        cmd += ['-H','Content-Type: application/json','-d',json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try: data = json.loads(pathlib.Path('/tmp/w3body').read_text())
    except Exception: data = {}
    return int(code or 0), data

def login_as(who):
    jar = f'/tmp/w3-{who}.jar'
    pathlib.Path(jar).unlink(missing_ok=True)
    code, d = call('/api/login', 'POST', {'person_key': who, 'pin': PINS[who]}, jar)
    return jar, code, d

print("1. the login roster is public but minimal\n")
code, d = call('/api/people')
ck('/api/people is reachable unauthenticated', code == 200, f'HTTP {code}')
ck('lists only people who can actually sign in', {p['person_key'] for p in d['people']} == set(PINS),
   str(sorted(p['person_key'] for p in d['people'])))
leak = json.dumps(d)
ck('exposes no addresses, roles or account state',
   '@' not in leak and 'role' not in leak and 'disabled' not in leak)

print("\n2. per-person sign-in\n")
for who, role in [('spencer','owner'), ('tega','admin'), ('martin','member')]:
    jar, code, d = login_as(who)
    ck(f'{who} signs in and the session says who + what', code == 200 and d.get('person_key') == who and d.get('role') == role,
       f"HTTP {code} person={d.get('person_key')} role={d.get('role')}")

print("\n3. wrong / disabled credentials\n")
jar = '/tmp/w3-bad.jar'
code, d = call('/api/login','POST',{'person_key':'spencer','pin':'zzzz-zzzz-zzzz'}, jar)
ck('wrong PIN is 401', code == 401, f'HTTP {code}')
code, d = call('/api/login','POST',{'person_key':'nobody','pin':'zzzz-zzzz-zzzz'}, jar)
ck('unknown person is 401 with the SAME message (no key enumeration)',
   code == 401 and d.get('detail') == 'That PIN is not right.', d.get('detail'))
code, d = call('/api/login','POST',{'person_key':'eddie','pin':'zzzz-zzzz-zzzz'}, jar)
ck('person with no PIN set is 401, indistinguishable', code == 401)

print("\n4. THE OLD SHARED PASSPHRASE NO LONGER OPENS A PERSON SESSION\n")
env = pathlib.Path('/root/.hermes/.env').read_text()
shared = [l.split('=',1)[1].strip().strip('"') for l in env.splitlines() if l.startswith('APP_PASSPHRASE=')][0]
jar = '/tmp/w3-op.jar'; pathlib.Path(jar).unlink(missing_ok=True)
code, d = call('/api/login','POST',{'passphrase': shared}, jar)
ck('it still works — as BREAK-GLASS', code == 200, f'HTTP {code}')
ck('it is NOT a person', d.get('person_key') is None, f"person_key={d.get('person_key')}")
ck('it is role=operator, not owner', d.get('role') == 'operator', f"role={d.get('role')}")

print("\n5. ROLE ENFORCEMENT IS SERVER-SIDE\n")
for who, expect in [('spencer',200), ('tega',403), ('martin',403)]:
    jar, _, _ = login_as(who)
    code, d = call('/api/approvals', jar=jar)
    ck(f'{who} -> approvals HTTP {code} (expected {expect})', code == expect, d.get('detail','')[:52])
jar = '/tmp/w3-op.jar'
code, d = call('/api/approvals', jar=jar)
ck('break-glass operator is refused approvals too (may read, must not approve)', code == 403, f'HTTP {code}')

print("\n6. promote / demote takes effect\n")
subprocess.run([sys.executable,'-m','app.accounts_cli','promote','martin','--role','admin'],
               cwd='/root/jarvis-app', capture_output=True)
jar, _, d = login_as('martin')
ck('martin is admin after promotion', d.get('role') == 'admin', f"role={d.get('role')}")
code, _ = call('/api/approvals', jar=jar)
ck('...and still 403s on approvals (admin != owner)', code == 403, f'HTTP {code}')
subprocess.run([sys.executable,'-m','app.accounts_cli','promote','martin','--role','member'],
               cwd='/root/jarvis-app', capture_output=True)
jar, _, d = login_as('martin')
ck('demoted back to member', d.get('role') == 'member', f"role={d.get('role')}")

print("\n7. the last owner cannot be demoted or disabled\n")
r = subprocess.run([sys.executable,'-m','app.accounts_cli','promote','spencer','--role','member'],
                   cwd='/root/jarvis-app', capture_output=True, text=True)
ck('demoting the only owner is refused', r.returncode == 2 and 'only owner' in r.stderr, r.stderr.strip()[:60])
r = subprocess.run([sys.executable,'-m','app.accounts_cli','disable','spencer'],
                   cwd='/root/jarvis-app', capture_output=True, text=True)
ck('disabling the only owner is refused', r.returncode == 2, r.stderr.strip()[:60])

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
