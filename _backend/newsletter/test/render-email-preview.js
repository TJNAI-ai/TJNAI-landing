// Writes preview/confirm-email.html with a sample (fake) confirm link. No network.
import { writeFileSync } from "node:fs";
import { renderConfirmEmail } from "../src/email.js";
const { subject, html, text } = renderConfirmEmail({ firstName: "Sam", confirmUrl: "https://tob-newsletter.REPLACE-ME.workers.dev/confirm?t=v1.PREVIEW-ONLY.0.not-a-real-token" });
writeFileSync(new URL("../preview/confirm-email.html", import.meta.url), html);
writeFileSync(new URL("../preview/confirm-email.txt", import.meta.url), `Subject: ${subject}\nFrom: Tim <tim@tjnai.ai>\nReply-To: info@tjnai.ai\n\n${text}\n`);
console.log("wrote preview/confirm-email.html and .txt");
