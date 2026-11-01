# license.py
import hmac
import hashlib
import base64
import json
import os
import uuid
import time
from datetime import date
from pathlib import Path

SECRET = b"KAVIN_FAST_SPEED_2024_SECRET_KEY_CHANGE_ME"
LICENSE_FILE = Path.home() / ".kavin_license.json"

def get_device_id():
    try:
        aid = os.popen("settings get secure android_id 2>/dev/null").read().strip()
        if aid and aid != "null":
            return hashlib.sha256(aid.encode()).hexdigest()[:12].upper()
    except Exception:
        pass
    try:
        mac = uuid.getnode()
        return hashlib.sha256(str(mac).encode()).hexdigest()[:12].upper()
    except Exception:
        return hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()[:12].upper()

def verify_key(key, device_id):
    try:
        if not key.startswith("KAVIN-"):
            return False, "Invalid format"
        parts = key.split("-")
        if len(parts) != 3:
            return False, "Invalid format"
        _, token, sig = parts
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        expect = hmac.new(SECRET, raw, hashlib.sha256).hexdigest()[:16].upper()
        if not hmac.compare_digest(expect, sig):
            return False, "Invalid signature"
        payload = json.loads(raw.decode())
        if payload.get("d") != device_id:
            return False, "Key is for another device"
        exp = date.fromisoformat(payload["e"])
        if date.today() > exp:
            return False, f"Expired on {exp.isoformat()}"
        left = (exp - date.today()).days
        return True, f"Valid — {left} days left (exp {exp.isoformat()})"
    except Exception as e:
        return False, f"Error: {e}"

def check_approval():
    print("\n" + "═" * 50)
    print("  ⚡  KAVIN  ⚡")
    print("  License Check")
    print("═" * 50 + "\n")

    dev = get_device_id()

    if LICENSE_FILE.exists():
        try:
            with open(LICENSE_FILE) as f:
                data = json.load(f)
            key = data.get("key", "")
            ok, msg = verify_key(key, dev)
            if ok:
                print(f"✅ {msg}")
                time.sleep(1)
                return True
            else:
                print(f"❌ {msg}")
                print("📤 Key အသစ် လိုပါတယ်။")
        except Exception:
            pass

    print(f"📱 သင့် Device ID:\n\n   {dev}\n")
    print("─" * 50)
    print("📤 ဒီ ID ကို Admin ဆီ ပို့ပါ။")
    print("   Admin က Key ပြန်ပို့ပါလိမ့်မယ်။")
    print("─" * 50 + "\n")

    key = input("➜ Key ကို ဒီမှာ ထည့်ပါ: ").strip()
    ok, msg = verify_key(key, dev)
    if ok:
        print(f"\n✅ {msg}")
        try:
            with open(LICENSE_FILE, "w") as f:
                json.dump({"key": key, "device": dev}, f)
        except Exception:
            pass
        time.sleep(1)
        return True
    else:
        print(f"\n❌ {msg}")
        input("Enter နှိပ်ပြီး ထွက်ပါ...")
        return False
