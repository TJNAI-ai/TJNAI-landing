// Confirm email for tjnai.ai/newsletter signups (double opt-in).
// Based on the approved Gumroad confirm template (projects/newsletter/templates/tob-confirm.*),
// with the first lines and the "why you got this" footer changed for the site form.

export const CONFIRM_SUBJECT = "Confirm you want Team of Bots";
export const POSTAL_ADDRESS = "5540 Centerview Dr Ste 204, PMB #588341, Raleigh, NC 27606-8012";

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const P = "padding-top:6px;padding-bottom:6px;font-family:Georgia,'Times New Roman',serif;font-size:17px;line-height:26px;color:#1f2933;";
const F = "font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px;color:#6b7280;";

export function renderConfirmEmail({ firstName, confirmUrl }) {
  const hi = firstName ? `Hi ${firstName},` : "Hi there,";
  const url = escapeHtml(confirmUrl);
  const html = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta http-equiv="X-UA-Compatible" content="IE=edge"><title>Team of Bots</title></head><body style="margin:0;padding:0;background-color:#ffffff;"><div style="display:none;max-height:0px;overflow:hidden;">One click and you're in.</div><table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff"><tr><td align="center" style="padding:24px 16px;"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">`
    + `<tr><td style="${P}">${escapeHtml(hi)}</td></tr>`
    + `<tr><td style="${P}">Thanks for signing up for Team of Bots at tjnai.ai. It's my free email every other Tuesday at 7 AM ET about how I run a small business with a team of AI bots.</td></tr>`
    + `<tr><td style="${P}">Tap the button to confirm it's you:</td></tr>`
    + `<tr><td style="padding-top:12px;padding-bottom:12px;"><table cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#14213d" style="background-color:#14213d;padding:12px 22px;"><a href="${url}" style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:20px;color:#ffffff;font-weight:bold;text-decoration:none;">Yes, sign me up</a></td></tr></table></td></tr>`
    + `<tr><td style="${P}">The link works for 7 days. If you didn't ask for this, ignore it and you won't hear from me.</td></tr>`
    + `<tr><td style="padding-top:14px;padding-bottom:6px;font-family:Georgia,'Times New Roman',serif;font-size:17px;line-height:26px;color:#1f2933;">— Tim · info@tjnai.ai</td></tr>`
    + `<tr><td style="padding-top:28px;border-top:1px solid #e5e7eb;${F}"><p style="margin:16px 0 8px;${F}">You're getting this one-time email because this address was entered at tjnai.ai/newsletter. If you don't confirm, you won't get the newsletter.</p><p style="margin:0 0 8px;${F}">TJNAI.ai LLC · ${escapeHtml(POSTAL_ADDRESS)}</p><p style="margin:0 0 8px;${F}">Button not working? Paste this link into your browser:<br><a href="${url}" style="color:#6b7280;text-decoration:underline;word-break:break-all;">${url}</a></p></td></tr>`
    + `</table></td></tr></table></body></html>`;

  const text = `${hi}

Thanks for signing up for Team of Bots at tjnai.ai. It's my free email every other Tuesday at 7 AM ET about how I run a small business with a team of AI bots.

Confirm it's you: ${confirmUrl}

The link works for 7 days. If you didn't ask for this, ignore it and you won't hear from me.

— Tim · info@tjnai.ai

--

You're getting this one-time email because this address was entered at tjnai.ai/newsletter. If you don't confirm, you won't get the newsletter.

TJNAI.ai LLC · ${POSTAL_ADDRESS}`;

  return { subject: CONFIRM_SUBJECT, html, text };
}
