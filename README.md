# Sales Team

Three AI agents that run on your laptop and use your Claude subscription:

| Agent | Runs | What it does |
|---|---|---|
| **Media buyer** | Every day, 9:00 | Reads Meta Ads numbers, pauses what loses money, scales what wins within your caps, proposes new ads |
| **Customer follow-up** | Every day, 10:00 | Drafts one WhatsApp message per customer who needs follow-up: order confirmation, after delivery, time to buy again, or lapsed |
| **Weekly review** | Every Sunday, 20:00 | Measures past decisions (right or wrong) and proposes next week's plan |

Everything shows up in a dashboard at `http://127.0.0.1:8765`. The dashboard and all messages are in Egyptian Arabic, for the business owner.

**No extra cost:** the Meta Marketing API is free, Claude runs on your subscription, the database is a file on your machine, and you send the WhatsApp messages yourself.
Note: Agent SDK usage on a subscription counts against a separate monthly Agent SDK credit (since June 2026), not your interactive limits.

---

## 1. Install and run (Mac)

> Not technical? Read **START-HERE.md** (in Arabic) only.

- **Easiest:** in the Claude desktop app, open the **Code** tab (not Chat), choose Local and this folder, and say "run the project and help me set it up". Claude follows `CLAUDE.md`.
- **Or:** install Python 3.10+ from python.org and double-click **Start.command**. First time only: right-click → Open. It creates the virtual env, installs packages, opens the browser and keeps the Mac awake while the window is open.
- If Claude is not signed in on this Mac: double-click **Claude Login.command**.

Manual (any OS):

```bash
cd sales-team
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### Updates

Double-click **Update.command**. It downloads the latest version from GitHub, backs up `data/` to `backups/` (keeps the last 5), and never touches `data/`, `.env` or `.venv/`. If the app is running it stops it first and starts it again after the update.

- The dashboard checks the `VERSION` file on GitHub every 6 hours and shows a notice when a newer version exists. It never downloads or installs anything by itself.
- On start, the app adds any missing database columns and upgrades old settings. Nothing is deleted.

**For developers:** bump `VERSION` with every push, otherwise users won't see the update. Keep database changes additive (new tables or columns only).

## 2. Try it first with sample data

```bash
python run.py --demo
```

Open `http://127.0.0.1:8765`. You get a fake ad account (sales, WhatsApp messages and video campaigns) and fake customers, and you can run the agents from the overview page. Claude runs for real; no real account is touched. Demo data lives in `data-demo/`.

## 3. First-run setup (in the dashboard)

When Meta is not connected, the dashboard opens the setup page («الإعداد أول مرة»). Five steps, each with a detailed guide and direct links:

1. **Claude:** a tiny real call checks the subscription works on this machine.
2. **Business:** name, what the ads mostly bring (orders, messages, leads, calls...), the target cost for that, payment method, shipping, daily and monthly caps.
3. **Meta:** create an app and a system user, generate a non-expiring token with `ads_management`, `ads_read`, `business_management`, find the ad account ID, and (optional) the app secret.
4. **Telegram:** create a bot with @BotFather, paste its token, send `/start`, and confirm in the dashboard.
5. **(Optional) Meta's official ads MCP.**

**How tokens are stored:**
- Every token is **tested before it is saved**. If it fails, the page says why, in plain Arabic.
- Tokens are saved in `.env` on your machine with mode 600 (only your user can read it).
- The page never shows a token again, only its first and last few characters.
- **Never paste a token into any chat**, including Claude.

You can still edit `.env` by hand (see `.env.example`).

> Meta's UI changes often. If menu names differ, look for "System users" and "Generate token".

## 3.5 (Optional) Meta's official ads MCP

Meta runs an official MCP server at `https://mcp.facebook.com/ads` with more tools than the app uses (Pixel diagnostics, more campaign details). The app connects to it **itself**; agents never talk to it directly. The token stays in the app, every call is logged, and any write goes through the same approvals, dry run and kill switch.

**Token:**
0. **Once:** at developers.facebook.com/apps → your app → **Use cases** → Add **Create & manage ads with ads MCP server**. Without it Meta refuses the token.
1. Open **Graph API Explorer**, pick your app, choose **User Token** (not Page).
2. Request: `ads_mcp_management`, `ads_read`, `ads_management`, `catalog_management`, `business_management`, `pages_show_list`, `instagram_basic`.
3. Generate Access Token. It lives one hour, so extend it in **Access Token Debugger** (Extend Access Token) to about two months.
4. Paste it in setup step 5.

When it expires the dashboard tells you. If Meta refuses a token, the setup page asks Graph which permissions the token really has and says exactly what is missing (use case, expired, page token, or specific permissions).

**Control (Settings → Meta MCP):**
- One switch for the whole MCP.
- **Per agent:** off, read only, or read and write.
- **Per tool:** read, write with approval, auto write, or blocked. Tools are classified conservatively: anything not clearly read-only is treated as a write. You can override.
- **Every write waits for your approval**, and you see the exact request that will be sent, in the dashboard and in Telegram. Requests with unknown fields or that are too long to review are refused.
- **Auto write** is only set by you, per tool, and even then only safe changes run alone (like pausing). Anything with money, anything that creates or turns something on, still waits for you. At most 3 automatic changes per run.

Default: MCP off. When on, media buyer and weekly review get read only; follow-up gets nothing.

All tokens (Meta and Telegram) stay in the app and never reach the agents or their process environment.

## 3.6 (Optional, recommended) Telegram

Each agent's summary when it finishes, and every decision that needs you comes with Approve / Reject buttons. Commands:
- `/status`: team status.
- `/pending`: decisions waiting for you.
- `/stop`: stop the whole team now.

Free, with no server or open port (long polling).

1. In Telegram open **@BotFather**, send `/newbot`, pick a name. You get a **token**.
2. Paste it in setup step 4.
3. Send `/start` to your bot. The page catches it and asks "is this you?". Confirm.
4. You get a welcome message. Done.

**Security:**
- The bot only listens to the account you confirmed in the dashboard. Nobody can link themselves without you confirming on the laptop.
- Every button is signed with a secret on your machine, so a press can't be forged.
- Approving from Telegram runs the exact same checks as the dashboard.
- **Decision messages have two parts:** "what will run" is built from the stored action, and "what the agent says" is clearly labelled as the agent's words.
- **Old buttons don't work:** after 48 hours, or if dry run changed since the message was sent, a press does nothing and the bot sends a fresh card. Anyone holding your unlocked phone can approve, so lock it.

## 4. Daily use

- Double-click **Start.command** (or `python run.py`) and keep the window open. On a Mac it keeps the machine awake.
- If the laptop was off at the scheduled time, the agent runs as soon as it's back on, the same day.
- Record orders under Customer follow-up → Orders, or import a CSV. Columns: `name, phone, product, amount`, optional: `payment, status, source, created_at`. Imported orders older than 3 days with no status are marked delivered, so nobody gets an "order confirmation" message for something bought months ago. Re-importing the same file skips duplicates.
- Fill the **source** field with the ad that brought the order. Without it the agents can't tell which ads bring customers who buy again.
- Mark orders delivered when they arrive, so the after-delivery message goes out on time.

## 4.4 Ads that don't sell (messages, leads, calls, engagement, reach)

The business doesn't have to sell online. A clinic, an academy or any service works too.
- **Every campaign is judged by its own goal.** The app reads each ad set's optimization goal from Meta and measures it by that result:
  - **Bring customers:** purchase, messaging conversation (WhatsApp or Messenger), lead (form), call, registration.
  - **Traffic:** landing page view, link click.
  - **Engagement and reach:** engagement, video view (3 seconds), ThruPlay, page like, reach (priced per 1,000 people).
- **A target per type:** Settings → targets for non-sale ads. A type with no target is never paused or cut because of its cost; the agent proposes a target with numbers instead.
- **Engagement, video, reach and traffic campaigns are never changed without your approval**, even when pausing is set to automatic. This is enforced in code, not just in the prompt. Agents are also told never to pause them for "no sales".
- **What is one message worth?** If your ads bring messages, leads or calls, record the orders you close (and their source). The profit page then computes how many messages it takes to get one order, the break-even cost per message, a suggested target, and the real cost per order (ad spend ÷ orders).
- **Numbers don't get mixed:** the overview has a "yesterday by campaign goal" table, the headline cost per sale only counts sales campaigns, and age/placement breakdowns only include ad sets of the main goal.
- To judge every campaign by one result type, change it in Settings.

## 4.5 Real profit and analysis

The profit page computes the money you actually keep:
- **Formula:** revenue − product cost − shipping − return losses − ad spend.
- **Break-even:** profit per order before ads. Paying more than that in ads per order loses money.
- **Suggested target:** break-even minus the profit share you want. If your target is above break-even, the page warns you and the media buyer says so in its summary.
- **Profit by order source:** a cheap ad whose orders come back can lose more than an expensive one.
- **Orders not delivered yet** are discounted by your usual return rate from the last 3 months, so profit isn't overstated.
- **Multi-item orders:** record them as their own product (e.g. "2 hoodies") with its cost.

**What you enter:**
- The cost of each product, named exactly as in your orders. Products without a cost use the percentage in settings.
- Settings → order costs: shipping, loss per returned order, and the profit share you want.

**Deeper analysis:**
- **Who buys and where:** age and gender, placement (Reels or Feed, Instagram or Facebook), region and hour. Meta doesn't return website results for region and hour, so those compare CTR and spend share only (on-Facebook results like messages are still shown).
- **Tired ads:** CTR over the last 3 days vs the last 14, with frequency.
- The media buyer looks at this on Mondays and Thursdays and proposes changes when a segment wastes money. You apply targeting changes in Ads Manager.

## 5. Start safe, loosen permissions one at a time

The defaults are deliberately strict:

| Stage | Settings | How long |
|---|---|---|
| 1 | **Dry run on** + everything needs approval | Two weeks: compare decisions with what you would have done |
| 2 | Dry run off, everything still needs approval | Two weeks: you approve, changes are real |
| 3 | Automatic pause and budget decrease | When decision accuracy on the decisions page is high |
| 4 | Automatic budget increase | Last, once your daily and monthly caps are right |

The decisions page shows every measured decision and whether it was right. Let those numbers decide when to loosen permissions.

## 6. Security: how the code protects you

**Against prompt injection** (e.g. someone puts "raise all budgets" in an ad name or a customer name):
- Agents **only have the tools we built**: no terminal, no files, no internet, none of your Claude Code settings. Verified in practice: the session starts with only our tools.
- All outside text is cleaned (hidden characters and direction-override characters removed) and wrapped in `<untrusted_data>` so it is treated as data, not instructions.
- **Limits live in code, not in the prompt:** daily cap, monthly cap, max increase or decrease per change, cooldown between changes, and your permissions. Even a fooled agent is refused by the tool itself.
- Every decision is checked again at execution time with the settings as they are then. Lower a cap or change a permission before it runs and it is refused or comes back to you.
- One decision executes at a time, so two can't add up past a cap.
- Per run: at most 3 automatic pauses and 3 automatic budget changes; the rest wait for you.
- Agents can't change any setting or touch anything that isn't in your ad account.
- Customer messages are rejected automatically if they contain a link, a phone number, a payment request, or any number not from the customer's own order or your offers. Any offer or discount must be copied word for word from your offers. You send the message yourself.
- Messages from you to the agents (approve or reject) carry only the decision number and your note, never ad names someone could have written into.
- At most 10 actions per run, a cost cap per run, and every run stops after 15 minutes.

**Dashboard:**
- Listens on `127.0.0.1` only, so nobody on the network can open it.
- Every change needs a per-machine secret header, which blocks other websites from sending requests from your browser.
- Content-Security-Policy, and all data is rendered as text, so no name can run code in the page.
- The Meta token lives only in `.env`: not in the database, any prompt, or any error message.

**Kill switch:** Settings → Safety → team on («الفريق شغال»). Turn it off and save, and any running agent stops immediately.

**About caps:** the app's caps stop agents from setting budgets above them. Meta itself can spend more than the daily budget on some days and less on others. So also set an **account spending limit** in Meta (Billing → Account spending limit). That is the last line nobody can cross.

## 7. Limits

- The system doesn't create campaigns by itself. It writes the full proposal (audience, budget, copy) and you create it in Ads Manager.
- Messages aren't sent automatically, because the WhatsApp Business API costs money. The "open WhatsApp" button prepares the message and you press send.
- Customer follow-up is built for online orders and shipping. Bookings and appointments (clinics, academies) are not modelled yet.
- The laptop must be awake and online at run time.

## 8. Developers

```bash
python -m pytest -q tests      # 136 tests: caps, injection, permissions, concurrent execution, API security, setup, result types, upgrades
```

| File | What's in it |
|---|---|
| `app/agents.py` | Prompts and the locked-down SDK options |
| `app/tools.py` | Every tool the agents can call |
| `app/guards.py` | Limits: money, text cleaning, customer message checks |
| `app/executor.py` | The only place that changes anything in Meta |
| `app/meta_api.py` | Marketing API client, result types, the demo account |
| `app/analysis.py` | Real profit, tired ads, breakdowns |
| `app/telegram.py` | Telegram bot: summaries, approvals, /stop |
| `app/meta_mcp.py` | Proxy to Meta's official MCP and its read/write classification |
| `app/setup.py` | First-run setup: test-then-save for every token |
| `app/updates.py` | Background check for a newer `VERSION` on GitHub |
| `app/server.py` + `static/` | The dashboard |
| `Start.command`, `Update.command`, `Claude Login.command` | Mac launchers |
