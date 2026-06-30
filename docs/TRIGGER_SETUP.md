# Reliable On-Time Triggers — Setup Guide

## Why this is needed

GitHub Actions `schedule:` (cron) is **best-effort by design**. GitHub's own docs
say scheduled runs can be delayed 5–30+ minutes (occasionally dropped) during
high load. There is **no GitHub setting** to make it punctual. For a stock-market
tool where a 9:35 AM run firing at 2:22 PM is useless, we trigger the workflow
from an **external scheduler** that fires on time and calls GitHub's API.

**Architecture (defense in depth):**
- **Primary:** an external web-cron (cron-job.org — free, reliable) POSTs to
  GitHub at the exact IST times → workflow runs within seconds of schedule.
- **Fallback:** the GitHub `schedule:` crons stay in place, so if the external
  service ever fails, GitHub still runs it (late, but it runs).

The workflow accepts a `repository_dispatch` event of type `run-session` with the
exact session in the payload, so the external trigger runs the precise session
with no time-window guessing.

---

## One-time setup (~10 minutes)

### Step 1 — Create a fine-grained GitHub token (PAT)

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
2. Name: `nse-trigger`. Expiration: 1 year (set a reminder to rotate).
3. **Repository access** → Only select repositories → `nse-ai-trader`.
4. **Permissions** → Repository permissions → **Contents: Read and write** AND
   **Actions: Read and write** (Actions write is what allows dispatch).
   (Metadata: Read is auto-added — fine.)
5. Generate, copy the token (starts `github_pat_...`). You won't see it again.

### Step 2 — Create the 5 scheduled jobs on cron-job.org

1. Sign up free at https://cron-job.org → **Create cronjob**.
2. For EACH session below, create one job:

| Job title | IST time | Schedule (the site uses your local TZ; set TZ = Asia/Kolkata) | session |
|---|---|---|---|
| NSE pre-open  | 08:35 | every weekday 08:35 | `preopen`   |
| NSE morning   | 09:35 | every weekday 09:35 | `morning`   |
| NSE midday    | 11:45 | every weekday 11:45 | `midday`    |
| NSE afternoon | 13:15 | every weekday 13:15 | `afternoon` |
| NSE preclose  | 15:40 | every weekday 15:40 | `preclose`  |

> **Preclose is 15:40 IST on purpose** — *after* the 15:30 market close — so it
> captures the full session including the closing-auction move. Do NOT set it to
> 15:0x (e.g. 15:02), which misses the last ~28 minutes of trading.

3. For each job's **Request settings**:
   - **URL:** `https://api.github.com/repos/deepkhara09-arch/nse-ai-trader/dispatches`
   - **Request method:** `POST`
   - **Headers:**
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer <YOUR_PAT>`
     - `X-GitHub-Api-Version: 2022-11-28`
   - **Request body** (change `session` per job):
     ```json
     {"event_type":"run-session","client_payload":{"session":"morning"}}
     ```
   - In the schedule, restrict to **Mon–Fri** (NSE is closed weekends; the tool
     also has its own weekend guard as a backstop).

4. Save. cron-job.org shows execution history so you can confirm each fired.

### Step 3 — Verify

- Trigger one job manually from cron-job.org (or wait for the next slot).
- GitHub → Actions tab should show a run started by `repository_dispatch`.
- The dashboard's **Run Log** will show the exact IST time it fired + a summary.

---

## Notes

- The GitHub `schedule:` crons remain as a free fallback — no action needed.
- If you ever rotate/lose the PAT, the fallback crons keep the tool alive (just
  less punctual) until you update the token on cron-job.org.
- Weekends: jobs are weekday-only, and the tool skips trading on weekends anyway.
- The external trigger passes the exact session, so timing is precise to the second
  the scheduler fires — no dependence on GitHub's flaky cron timing.
