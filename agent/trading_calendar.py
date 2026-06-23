"""
NSE trading-calendar helper.

A "trading day" = a weekday that is NOT an NSE holiday. The phase day-counter and
any "is the market open" logic uses this so holidays and weekends never count, and
phases naturally take as many calendar days as needed to accumulate enough real
trading days.

NSE publishes its holiday list yearly. We keep a maintained static list (equities
segment full-day holidays). It's easy to extend each year — add the dates and
you're done. If a year isn't listed yet, we fall back to weekday-only (safe: at
worst a holiday is counted, which the data layer also guards since fetches return
empty on a closed day).
"""

from datetime import date, datetime, timezone, timedelta

# NSE operates on India Standard Time (UTC+5:30). GitHub Actions runs in UTC, so
# every date/time decision in this tool MUST be computed in IST — otherwise a run
# near the date boundary could read the wrong calendar day (wrong holiday/weekend
# check, wrong "today" for the day counter). ist_today() is the single source of
# truth for "what date is it for NSE right now".
IST = timezone(timedelta(hours=5, minutes=30))


def ist_today() -> date:
    """Today's date in IST (NSE timezone) — use this everywhere instead of
    date.today(), which would return the server's UTC date on GitHub Actions."""
    return datetime.now(IST).date()


def ist_now() -> datetime:
    """Current datetime in IST."""
    return datetime.now(IST)


# NSE equity-segment trading holidays (full-day). Extend yearly from the official
# NSE holiday circular. Format: "YYYY-MM-DD".
NSE_HOLIDAYS = {
    # ── 2026 (NSE equity holidays) ──────────────────────────────────────────────
    "2026-01-26",  # Republic Day
    "2026-02-15",  # Maha Shivaratri (observed)
    "2026-03-04",  # Holi
    "2026-03-21",  # Id-ul-Fitr (approx)
    "2026-03-31",  # Ram Navami (approx)
    "2026-04-01",  # Annual bank closing / Mahavir Jayanti (approx)
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-06-17",  # Bakri Id (approx)
    "2026-08-15",  # Independence Day
    "2026-08-28",  # Ganesh Chaturthi (approx)
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-21",  # Dussehra (approx)
    "2026-11-09",  # Diwali / Laxmi Pujan (approx; muhurat session separate)
    "2026-11-24",  # Guru Nanak Jayanti (approx)
    "2026-12-25",  # Christmas

    # ── 2027 (placeholder fixed-date holidays; refine from official circular) ────
    "2027-01-26",  # Republic Day
    "2027-08-15",  # Independence Day (Sunday in 2027 — harmless)
    "2027-10-02",  # Gandhi Jayanti
    "2027-12-25",  # Christmas
}


def is_trading_day(d: date = None) -> bool:
    """True only if d is a weekday AND not an NSE holiday. Defaults to IST today."""
    d = d or ist_today()
    if d.weekday() >= 5:          # Sat/Sun
        return False
    if d.isoformat() in NSE_HOLIDAYS:
        return False
    return True


def is_holiday(d: date = None) -> bool:
    """True if d is an NSE full-day holiday (weekday that's closed). IST default."""
    d = d or ist_today()
    return d.weekday() < 5 and d.isoformat() in NSE_HOLIDAYS


def reason_not_trading(d: date = None) -> str:
    """Human-readable reason a given day isn't a trading day (or ''). IST default."""
    d = d or ist_today()
    if d.weekday() >= 5:
        return "weekend"
    if d.isoformat() in NSE_HOLIDAYS:
        return "NSE holiday"
    return ""
