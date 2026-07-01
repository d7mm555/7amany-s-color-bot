/**
 * Google Apps Script web app backing the token system.
 * Deploy this bound to a Google Sheet with a tab named exactly "Tokens" with columns:
 *   A: Token   B: Status ("used" or blank)   C: DeviceID   D: RedeemedAt
 * See TOKENS.md in the project for step-by-step setup.
 */

function doGet(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Tokens');
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var action = e.parameter.action;
    if (action !== 'redeem') {
      return json({ok: false, error: 'unknown action'});
    }

    var token = (e.parameter.token || '').trim();
    var device = (e.parameter.device || '').trim();
    if (!token || !device) {
      return json({ok: false, error: 'missing token or device'});
    }

    var data = sheet.getDataRange().getValues();
    for (var i = 1; i < data.length; i++) {
      var rowToken = String(data[i][0]).trim();
      if (rowToken !== token) continue;

      var status = String(data[i][1] || '').trim().toLowerCase();
      var redeemedDevice = String(data[i][2] || '').trim();

      if (status === 'used') {
        // Same device re-verifying (e.g. reinstalled locally) is allowed; a different
        // device trying to reuse an already-redeemed token is rejected.
        if (redeemedDevice === device) {
          return json({ok: true});
        }
        return json({ok: false, error: 'Token already used'});
      }

      sheet.getRange(i + 1, 2).setValue('used');
      sheet.getRange(i + 1, 3).setValue(device);
      sheet.getRange(i + 1, 4).setValue(new Date());
      return json({ok: true});
    }

    return json({ok: false, error: 'Invalid token'});
  } finally {
    lock.releaseLock();
  }
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
