"""
OxinChain Full Auto Bot — Email/Password Login
================================================
Step 0: Login with email/password to get fresh token
Step 1: Claim daily coins
Step 2: Transfer verified balance to wallet
Step 3: Verify recipient username
Step 4: Send all wallet coins to recipient

pip install requests
python oxin_full.py            # loop every 12h
python oxin_full.py --once     # run once (GitHub Actions)
"""

import json, time, base64, logging, re, sys
from pathlib import Path
from datetime import datetime, timezone
import requests

# ── Config ────────────────────────────────────────────────────────────────────

ACCOUNTS_FILE  = "accounts.json"
CLAIM_INTERVAL = 12 * 60 * 60   # 12 hours (runs twice a day)

BASE             = "https://mine.oxinchain.io"
API_BASE         = "https://oxappapi.oxinchain.io/v1"   # real API base
URL_LOGIN        = f"{API_BASE}/auth/login.php"
URL_CLAIM        = f"{API_BASE}/user/claim-reward.php"   # ✔ fixed
URL_PROFILE      = f"{API_BASE}/user/profile.php"
URL_TRANSFER_BAL = f"{API_BASE}/user/transfer-balance.php"
URL_VERIFY       = f"{API_BASE}/wallet/verify-recipient.php"
URL_SEND         = f"{API_BASE}/wallet/send.php"

# ── Logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger("oxin")
log.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
for h in [logging.StreamHandler(), logging.FileHandler("oxin_full.log", encoding="utf-8")]:
    h.setFormatter(fmt)
    log.addHandler(h)

# ── Helpers ───────────────────────────────────────────────────────────────────

def base_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Origin":       "https://mine.oxinchain.io",
        "Referer":      "https://mine.oxinchain.io/",
    }


def auth_headers(token: str) -> dict:
    raw  = token.replace("Bearer ", "").strip()
    full = f"Bearer {raw}"
    h    = base_headers()
    h["x-auth-token"]  = full
    h["Authorization"] = full
    return h


def parse(resp: requests.Response) -> dict:
    code = resp.status_code
    raw  = resp.text.strip()
    try:
        data = resp.json()
        msg  = str(data.get("message") or data.get("msg") or
                   data.get("error")   or data.get("detail") or data)
        amount = data.get("amount") or data.get("coins") or data.get("balance")
        if not amount and isinstance(data.get("data"), dict):
            d2     = data["data"]
            amount = d2.get("amount") or d2.get("balance") or d2.get("coins")
    except Exception:
        data, msg, amount = {}, raw, None
    msg_low  = msg.lower()
    wait_m   = re.search(r'\d+h\s*\d+m|\d+:\d+:\d+|\d+h|\d+m', msg, re.IGNORECASE)
    wait_str = wait_m.group(0) if wait_m else ""
    already  = any(w in msg_low for w in
                   ["too early","already","wait","cooldown","not yet","please wait","next claim"]
                   ) and code in (200, 400, 409, 429)
    # handle both "success": true and "status": "success"
    success  = (not already) and (
        (isinstance(data, dict) and data.get("success") is True) or
        (isinstance(data, dict) and str(data.get("status","")).lower() in ("ok","success")) or
        (code in (200, 201) and any(w in msg_low for w in
         ["success","mined","collected","done","complete","ok","transferred","sent"]))
    )
    return {"code": code, "success": success, "already": already,
            "msg": msg, "wait": wait_str, "amount": amount,
            "raw": raw[:500], "data": data}

# ── IP / Location Info ────────────────────────────────────────────────────────

def log_runner_info() -> None:
    """Fetch and log the public IP and geolocation of the machine running the bot."""
    log.info("=" * 45)
    log.info("  🌐  Fetching runner IP & location...")
    try:
        resp = requests.get("https://ipinfo.io/json", timeout=10)
        if resp.status_code == 200:
            info     = resp.json()
            ip       = info.get("ip",       "unknown")
            city     = info.get("city",     "unknown")
            region   = info.get("region",   "unknown")
            country  = info.get("country",  "unknown")
            org      = info.get("org",      "unknown")
            timezone = info.get("timezone", "unknown")
            loc      = info.get("loc",      "unknown")
            log.info(f"  🌐  Runner IP      : {ip}")
            log.info(f"  📍  Location       : {city}, {region}, {country}")
            log.info(f"  🏢  ISP / Org      : {org}")
            log.info(f"  🕐  Timezone       : {timezone}")
            log.info(f"  🗺️   Coordinates    : {loc}")
        else:
            log.warning(f"  ⚠  ipinfo.io returned HTTP {resp.status_code}")
    except Exception as e:
        log.warning(f"  ⚠  IP lookup failed: {e}")
    log.info("=" * 45)

# ── Step 0: Login ─────────────────────────────────────────────────────────────

def step_login(email: str, password: str) -> str | None:
    """
    Login with email + password → return Bearer token.
    Uses oxappapi.oxinchain.io (real API) not mine.oxinchain.io.
    No CAPTCHA needed — slider is frontend-only, server ignores it.
    Sends static lat/lng to satisfy payload schema.
    """
    log.info("  [0/4] Logging in...")
    payload = {
        "identifier":   email,
        "password":     password,
        "login_method": "email",
        "latitude":     31.5204,    # static Lahore coords — server doesn't validate
        "longitude":    74.3587,
    }
    try:
        resp = requests.post(URL_LOGIN, json=payload,
                             headers=base_headers(), timeout=15)
    except Exception as e:
        log.error(f"  ✘  Login request error: {e}")
        return None

    log.debug(f"  Login [{resp.status_code}]: {resp.text[:300]}")

    if resp.status_code == 401:
        log.error("  ✘  LOGIN FAILED — wrong email or password.")
        log.error(f"     Check credentials in accounts.json for: {email}")
        return None

    if resp.status_code in (200, 201):
        data  = resp.json()
        # server returns: {"status":"success","message":"Login successful","token":"...","user":{...}}
        token = data.get("token") or data.get("access_token") or data.get("auth_token")
        if token:
            log.info(f"  ✔  Login successful!")
            return f"Bearer {token}"
        log.warning(f"  ⚠  HTTP 200 but no token in response: {resp.text[:300]}")
        return None

    p = parse(resp)
    log.error(f"  ✘  Login failed [{resp.status_code}]: {p['msg']}")
    return None

# ── Step 1: Claim ─────────────────────────────────────────────────────────────

def step_claim(session: requests.Session) -> bool:
    try:
        resp = session.post(URL_CLAIM, json={}, timeout=15)
    except Exception as e:
        log.error(f"  ✘  Claim error: {e}")
        return False
    p = parse(resp)
    if p["code"] == 401:
        log.error("  ✘  UNAUTHORIZED — token issue.")
        return False
    if p["already"] or p["code"] == 429:
        wait = f" (next claim in {p['wait']})" if p["wait"] else ""
        log.info(f"  ⏳  Already claimed today{wait}")
        return True
    if p["success"] or p["code"] in (200, 201):
        log.info("  ✔  Coins Claimed Successfully!")
        return True
    log.error(f"  ✘  Claim Failed: {p['msg']}")
    return False

# ── Step 2: Transfer verified balance to wallet ───────────────────────────────

def step_transfer_to_wallet(session: requests.Session) -> bool:
    log.info("  [2/4] Fetching verified balance...")
    verified_bal = 0.0
    try:
        resp = session.get(URL_PROFILE, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            try:
                user_info    = data["data"]["user_info"]
                verified_bal = float(user_info.get("verified_balance", 0))
                total_bal    = float(user_info.get("total_balance", 0))
                log.info(f"  Total: {total_bal} | Verified (transferable): {verified_bal}")
            except Exception:
                pass
    except Exception as e:
        log.warning(f"  Could not fetch profile: {e}")

    if verified_bal <= 0:
        log.warning("  ⚠  Verified balance is 0 — skipping transfer, continuing to send step.")
        return True

    log.info(f"  Transferring {int(verified_bal)} verified coins to wallet...")
    b = int(verified_bal)
    for payload in [{"amount": b}, {"amount": float(verified_bal)}, {"amount": str(b)}]:
        try:
            resp = session.post(URL_TRANSFER_BAL, json=payload, timeout=15)
        except Exception as e:
            log.error(f"  ✘  Transfer error: {e}")
            return False
        p = parse(resp)
        log.debug(f"  Payload {payload} → [{p['code']}]: {p['raw']}")
        if p["code"] == 401:
            log.error("  ✘  UNAUTHORIZED.")
            return False
        if p["success"] or p["code"] in (200, 201):
            log.info("  ✔  Transfer to wallet successful!")
            return True
        if any(w in p["msg"].lower() for w in ["nothing","empty","zero","no balance","insufficient"]):
            log.warning("  ⚠  No verified balance to transfer.")
            return True
        if "invalid amount" in p["msg"].lower():
            continue
    log.error("  ✘  Transfer to wallet failed.")
    return False

# ── Step 3: Get wallet balance ────────────────────────────────────────────────

def step_get_wallet_balance(session: requests.Session) -> float:
    wallet_urls = [
        f"{BASE}/api/wallet",
        f"{BASE}/api/wallet/balance",
        f"{BASE}/api/wallet/info",
        f"{BASE}/api/wallet/dashboard",
        f"{BASE}/api/user/wallet",
        f"{BASE}/api/user/profile",
    ]
    for url in wallet_urls:
        try:
            resp = session.get(url, timeout=12)
            if resp.status_code != 200:
                continue
            data = resp.json()
            def find_bal(d, depth=0):
                if depth > 4: return None
                if isinstance(d, dict):
                    for k in ["wallet_balance","balance","amount","coins",
                              "total","available","oxin_balance"]:
                        v = d.get(k)
                        if v is not None:
                            try:
                                f = float(v)
                                if f > 0: return f, k
                            except: pass
                    for v in d.values():
                        r = find_bal(v, depth+1)
                        if r: return r
                elif isinstance(d, list):
                    for item in d:
                        r = find_bal(item, depth+1)
                        if r: return r
                return None
            result = find_bal(data)
            if result:
                bal, key = result
                log.info(f"  ✔  Wallet balance ({key}): {bal}")
                return bal
        except Exception:
            continue
    log.warning("  ⚠  Could not fetch wallet balance.")
    return 0.0

# ── Step 4: Verify + Send ─────────────────────────────────────────────────────

def step_verify(session: requests.Session, username: str) -> bool:
    log.info(f"  [3/4] Verifying recipient '{username}'...")
    try:
        resp = session.post(URL_VERIFY,
                            json={"identifier": username, "method": "Username"},
                            timeout=15)
    except Exception as e:
        log.error(f"  ✘  Verify error: {e}")
        return False
    p = parse(resp)
    log.info(f"  Server [{p['code']}]: {p['raw']}")
    if p["code"] == 404 or "not found" in p["msg"].lower():
        log.error(f"  ✘  Username '{username}' NOT FOUND.")
        return False
    if p["success"] or p["code"] in (200, 201):
        log.info(f"  ✔  Recipient '{username}' verified!")
        return True
    log.error(f"  ✘  Verify failed: {p['msg']}")
    return False


def step_send(session: requests.Session, username: str, amount: float) -> bool:
    log.info(f"  [4/4] Sending {int(amount)} OXIN to '{username}'...")
    if not amount or amount <= 0:
        log.error("  ✘  Wallet balance is 0 — nothing to send.")
        return False
    payload = {
        "recipient":    username,
        "amount":       int(amount),
        "token_symbol": "OXIN",
        "method":       "Username"
    }
    try:
        resp = session.post(URL_SEND, json=payload, timeout=15)
    except Exception as e:
        log.error(f"  ✘  Send error: {e}")
        return False
    p = parse(resp)
    log.info(f"  Server [{p['code']}]: {p['raw']}")
    if p["code"] == 401:
        log.error("  ✘  UNAUTHORIZED.")
        return False
    if any(w in p["msg"].lower() for w in ["insufficient","not enough","empty","zero","no balance"]):
        log.warning("  ⚠  Wallet empty — nothing to send.")
        return False
    if p["success"] or p["code"] in (200, 201):
        log.info(f"  ✔  SEND SUCCESSFUL — {int(amount)} OXIN sent to '{username}'!")
        return True
    log.error(f"  ✘  Send failed — {p['code']}: {p['msg']}")
    return False

# ── Full pipeline ─────────────────────────────────────────────────────────────

def process_account(acc: dict) -> None:
    label       = acc.get("label", "Account")
    email       = acc.get("email", "").strip()
    password    = acc.get("password", "").strip()
    web3_wallet = acc.get("web3_wallet", "").strip()

    print()
    print("=" * 45)
    log.info(f"  Account    : {label}")
    log.info(f"  Email      : {email}")
    log.info(f"  Web3 Wallet: {web3_wallet if web3_wallet else '(not set)'}")
    print("=" * 45)

    if not email or not password:
        log.error("  ✘  No email/password in accounts.json — skipping.")
        return

    try:
        # 0. Login
        token = step_login(email, password)
        if not token:
            log.error("  ✘  Login failed — check email/password in accounts.json")
            return
        time.sleep(2)

        session = requests.Session()
        session.headers.update(auth_headers(token))

        # 1. Claim
        log.info("  [1/4] ⏳  Claiming daily coins...")
        if not step_claim(session):
            log.error("  ✘  Claim failed — stopping.")
            return
        time.sleep(2)

        # 2. Get total balance
        total_bal = 0.0
        try:
            for url in [URL_PROFILE, f"{BASE}/api/user/dashboard"]:
                resp = session.get(url, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        ui = data["data"]["user_info"]
                        total_bal = float(ui.get("total_balance") or 0)
                        if total_bal > 0:
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        log.info(f"  💰  Total Balance: {total_bal} OXIN")
        time.sleep(2)

        # 3. Transfer to Web3 wallet
        if not web3_wallet:
            log.warning("  ⚠  No web3_wallet set — skipping transfer.")
            return

        if total_bal <= 0:
            log.warning("  ⚠  Balance is 0 — nothing to transfer.")
            return

        log.info(f"  [3/4] ⏳  Sending {int(total_bal)} OXIN to Web3 wallet...")
        payload = {"amount": int(total_bal), "wallet_address": web3_wallet}
        try:
            resp = session.post(URL_TRANSFER_BAL, json=payload, timeout=15)
            p = parse(resp)
            if p["success"] or p["code"] in (200, 201):
                amount = p["amount"] or int(total_bal)
                tx = (p["data"].get("data") or {}).get("tx_hash", "") if isinstance(p["data"], dict) else ""
                log.info(f"  ✔  Transfer Successful!")
                log.info(f"  💸  {amount} OXIN sent to {web3_wallet}")
                if tx:
                    log.info(f"  🔗  TX Hash: {tx}")
            elif any(w in p["msg"].lower() for w in ["insufficient","empty","zero","no balance"]):
                log.warning("  ⚠  Insufficient balance to transfer.")
            else:
                log.error(f"  ✘  Transfer Failed: {p['msg']}")
        except Exception as e:
            log.error(f"  ✘  Transfer error: {e}")

    except Exception as e:
        log.error(f"  ✘  Unexpected error: {e}")

    print("=" * 45)

# ── Cycle & Main ──────────────────────────────────────────────────────────────

def run_cycle(accounts: list) -> None:
    log.info(f">>> Cycle: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {len(accounts)} account(s)")
    for i, acc in enumerate(accounts, 1):
        log.info(f">>> [{i}/{len(accounts)}]")
        process_account(acc)
    print()
    log.info(">>> All accounts processed.")


def main():
    once = "--once" in sys.argv
    path = Path(ACCOUNTS_FILE)
    if not path.exists():
        print("ERROR: accounts.json not found!")
        return
    with open(path, encoding="utf-8") as f:
        accounts = json.load(f)
    if not accounts:
        print("ERROR: No accounts in accounts.json.")
        return

    print()
    print("=" * 45)
    print("   OxinChain Full Auto Bot")
    print("=" * 45)
    print(f"   Accounts  : {len(accounts)}")
    print(f"   Login     : Email + Password (no CAPTCHA)")
    print(f"   Mode      : {'Run once (GitHub Actions)' if once else 'Loop every 12h'}")
    print(f"   Steps     : Login → Claim → Balance → Transfer to Web3")
    print(f"   Log file  : oxin_full.log")
    print("=" * 45)

    log_runner_info()

    if once:
        run_cycle(accounts)
        log.info(">>> Done. Exiting.")
    else:
        while True:
            run_cycle(accounts)
            log.info(">>> Next cycle in 12 hours. Press Ctrl+C to stop.")
            time.sleep(CLAIM_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
