/**
 * Doshi Labs: News — mailer (Google Apps Script)
 * ---------------------------------------------
 * Trivial + bulletproof: reads picks.json from the GitHub repo's raw URL and
 * emails a bare digest to CONFIG.RECIPIENTS. All fetching, curation, and
 * link-resolution happen in a separate daily job on GitHub Actions (a non-Google
 * IP, which is required — see HANDOFF.md gotcha #1). This script only touches
 * GitHub-raw + Gmail, so Google can never CAPTCHA-block it.
 *
 * SENDER: mail is sent as the Google account that OWNS this project and
 * authorized the trigger. This project must live under aaravpdoshi@gmail.com so
 * the From-address is aaravpdoshi@gmail.com. Do not run it from the Wharton
 * account — there is no from: override without a verified send-as alias.
 *
 * ONE-TIME SETUP:
 *   - Create the project under aaravpdoshi@gmail.com.
 *   - Project Settings → timezone America/New_York.
 *   - Run sendTestNow() and authorize, then run installTrigger() once.
 *   - Delete the old sendDaily trigger on the Wharton account (else 2 emails/day).
 */

// ---- CONFIG -----------------------------------------------------------------
var CONFIG = {
  // Raw URL of picks.json in your GitHub repo (GitHub Actions commits it daily).
  PICKS_URL: 'https://raw.githubusercontent.com/apdoshi-netizen/wsj-daily/main/picks.json',
  // Who receives the digest. First address is the To:, the rest are BCC'd.
  // Consumer Gmail allows 100 recipients/day via Apps Script.
  RECIPIENTS: ['apdoshi@wharton.upenn.edu'],
  SEND_HOUR: 9,                     // 9 AM in the project timezone (set to ET)
  SUBJECT_PREFIX: 'News',           // subject reads "News: 8/8/2026"
  SENDER_NAME: 'Doshi Labs',        // display name on the From line
  REQUIRE_FRESH: true,              // only send if the picks file is dated today
  ALERT_ON_MISSING: true            // email recipient[0] if no fresh picks
};
// -----------------------------------------------------------------------------

/** Main entry — called by the daily trigger. */
function sendDaily() {
  var recipients = getRecipients();
  if (recipients.length === 0) { Logger.log('No recipients; nothing sent.'); return; }

  var data = getTodaysPicks();
  var today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  if (!data || (CONFIG.REQUIRE_FRESH && data.date !== today)) {
    Logger.log('No fresh picks for ' + today + ' (found: ' + (data ? data.date : 'none') + ').');
    if (CONFIG.ALERT_ON_MISSING) {
      GmailApp.sendEmail(recipients[0], CONFIG.SUBJECT_PREFIX + ': no digest today',
        'No picks dated ' + today + ' were available (found: ' + (data ? data.date : 'none') +
        '), so no digest was sent.', { name: CONFIG.SENDER_NAME });
    }
    return;
  }

  var email = buildEmail(data);
  GmailApp.sendEmail(recipients[0], email.subject, email.textBody, {
    htmlBody: email.htmlBody, bcc: recipients.slice(1).join(','), name: CONFIG.SENDER_NAME
  });
  Logger.log('Sent to ' + recipients.length + ' recipient(s).');
}

/** Fetch picks.json from GitHub. Returns {date, picks} or null. */
function getTodaysPicks() {
  try {
    var resp = UrlFetchApp.fetch(CONFIG.PICKS_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
    if (resp.getResponseCode() !== 200) { Logger.log('picks fetch HTTP ' + resp.getResponseCode()); return null; }
    return JSON.parse(resp.getContentText());
  } catch (e) { Logger.log('picks fetch/parse failed: ' + e); return null; }
}

/** Bare digest email (matches the plain WSJ layout). */
function buildEmail(data) {
  var pretty = Utilities.formatDate(new Date(data.date + 'T12:00:00'),
    Session.getScriptTimeZone(), 'M/d/yyyy');
  var subject = CONFIG.SUBJECT_PREFIX + ': ' + pretty;

  var rows = data.picks.map(function (p) {
    var link = p.url
      ? '<a href="' + escapeHtml(p.url) + '" style="color:#0b57d0;text-decoration:none;">' + escapeHtml(p.title) + '</a>'
      : '<span style="color:#888;">No WSJ pick today.</span>';
    var sum = (p.summary && p.url)
      ? '<div style="color:#555;font-size:14px;margin-top:2px;">' + escapeHtml(p.summary) + '</div>' : '';
    return '<p style="margin:0 0 20px 0;"><strong>' + escapeHtml(p.label) + ':</strong> ' + link + sum + '</p>';
  }).join('\n');

  var research = (data.research || []).map(function (r) {
    var meta = [r.firm, r.show, r.duration].filter(function (x) { return x; }).join(' · ');
    var sum = r.summary
      ? '<div style="color:#555;font-size:14px;margin-top:2px;">' + escapeHtml(r.summary) + '</div>'
      : '';
    return '<p style="margin:0 0 20px 0;">' +
      '<span style="color:#888;font-size:13px;">' + escapeHtml(meta) + '</span><br>' +
      '<a href="' + escapeHtml(r.url) + '" style="color:#0b57d0;text-decoration:none;">' +
      escapeHtml(r.title) + '</a>' + sum + '</p>';
  }).join('\n');

  var researchBody = research ||
    '<p style="margin:0;color:#888;">No GS/JPM/MS publications today.</p>';

  // Both section headings share one style. The WSJ one omits the top rule and
  // the top margin, since nothing sits above it.
  var HEAD = 'font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:#444;';

  var htmlBody =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#111;line-height:1.4;">' +
      '<h3 style="' + HEAD + 'margin:0 0 14px 0;">Wall Street Journal</h3>' +
      rows +
      '<h3 style="' + HEAD + 'border-top:1px solid #ddd;padding-top:16px;' +
        'margin:28px 0 14px 0;">Podcasts</h3>' +
      researchBody +
    '</div>';

  var researchText = (data.research || []).map(function (r) {
    var meta = [r.firm, r.show, r.duration].filter(function (x) { return x; }).join(' · ');
    return meta + '\n' + r.title + ' — ' + r.url + (r.summary ? '\n' + r.summary : '');
  }).join('\n\n') || 'No GS/JPM/MS publications today.';

  var textBody = 'WALL STREET JOURNAL\n\n' + data.picks.map(function (p) {
    var line = p.label + ': ' + (p.url ? p.title + ' — ' + p.url : 'No WSJ pick today.');
    if (p.summary && p.url) line += '\n' + p.summary;
    return line;
  }).join('\n\n') + '\n\nPODCASTS\n\n' + researchText;

  return { subject: subject, htmlBody: htmlBody, textBody: textBody };
}

// ---- helpers ----------------------------------------------------------------

/** Valid addresses from CONFIG.RECIPIENTS, in order. */
function getRecipients() {
  var re = /[^\s@]+@[^\s@]+\.[^\s@]+/;
  return (CONFIG.RECIPIENTS || []).map(function (l) { return String(l).trim(); })
    .filter(function (l) { return re.test(l); }).map(function (l) { return l.match(re)[0]; });
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- one-time / utility -----------------------------------------------------

/** Install the daily 9 AM trigger. Run once, authorize when prompted. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendDaily') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendDaily').timeBased().everyDays(1)
    .atHour(CONFIG.SEND_HOUR).nearMinute(0).create();
  Logger.log('Daily trigger installed for ~' + CONFIG.SEND_HOUR + ':00 (project timezone).');
}

/** Send right now, ignoring the freshness check — for testing. */
function sendTestNow() {
  var data = getTodaysPicks();
  if (!data) { Logger.log('No picks file fetched from ' + CONFIG.PICKS_URL); return; }
  var email = buildEmail(data);
  var me = getRecipients()[0] || Session.getActiveUser().getEmail();
  GmailApp.sendEmail(me, '[TEST] ' + email.subject, email.textBody,
    { htmlBody: email.htmlBody, name: CONFIG.SENDER_NAME });
  Logger.log('Test sent to ' + me + ' (picks dated ' + data.date + ')');
}
