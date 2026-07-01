# Token system setup (Google Sheets)

The redeemable/used state of every token lives in a Google Sheet you control — nothing about
which tokens are valid is stored in the app's code. The app only ever calls a small Google Apps
Script "web app" URL that reads/writes that sheet.

## 1. Create the sheet
1. Create a new Google Sheet.
2. Rename the first tab to exactly `Tokens` (case-sensitive).
3. Row 1 headers: `Token | Status | DeviceID | RedeemedAt`.
4. Fill column A (from row 2 down) with token strings. You can generate some with:
   ```
   py generate_tokens.py 50
   ```
   Paste the output into column A. Leave Status/DeviceID/RedeemedAt blank — they fill in
   automatically as tokens get redeemed.

## 2. Deploy the Apps Script web app
1. In the sheet: **Extensions → Apps Script**.
2. Delete the default code and paste in the contents of [`apps_script/Code.gs`](apps_script/Code.gs).
3. Save the project.
4. **Deploy → New deployment → type: Web app.**
   - Execute as: **Me**
   - Who has access: **Anyone**
5. Click **Deploy**, approve the permission prompts (this is your own script accessing your own
   sheet), and copy the **Web app URL** it gives you.

## 3. Point the app at it
Open [`server_config.json`](server_config.json) in the project folder and paste the URL in:
```json
{
  "apps_script_url": "https://script.google.com/macros/s/XXXXXXXX/exec"
}
```
Save the file. No code changes needed — the app reads this file at startup.

## How it behaves
- A token can be redeemed exactly once. If a second device tries the same token, the sheet
  already shows it as `used` for a different device, so it's rejected.
- Once a device redeems a token successfully, the app caches that locally
  (`%APPDATA%\7amanys-color-bot\license.json`) and never asks for a token again on that machine.
- If the same device re-enters the same already-used token (e.g. after reinstalling), it's
  accepted again — it's checking "was this used by *me*", not just "is it used".

## Note on security
The web app URL is callable by anyone who has it (that's required for the app to reach it
without you hosting real API-key infrastructure). Don't publish the URL publicly beyond
distributing the app itself, and treat tokens as normal redemption codes rather than secrets.
