# TJNAI-landing — project instructions

The **live tjnai.ai website**. A hand-written static site (no framework, no
build step) served by GitHub Pages from `github.com/scratchcat69/TJNAI-landing`.

**This is production.** Every push to `main` deploys straight to tjnai.ai —
there is no staging environment and no review gate. The repo also hosts legal
and Universal Link infrastructure that a shipped App Store app depends on
(below). Preview locally before pushing.

## ⚠️ `tovael-privacy.html` is load-bearing — read this first

`tovael-privacy.html` is served at **`https://tjnai.ai/tovael-privacy`** (no
extension — GitHub Pages' `.html` fallback). That exact URL is:

- the Privacy Policy URL on the **live App Store listing** for Tovael
  (`com.tjnai.tovael`), which Apple requires to resolve, and
- linked from inside the shipped app — `Pool-Chemical-App/app/legal/privacy-policy.tsx`
  opens `https://tjnai.ai/tovael-privacy`.

**Do not rename, move, delete, or add an extension-changing redirect to this
file.** Breaking it breaks an App Store compliance requirement for an app
already in users' hands. Nothing on this site links to it, so it looks like an
orphan during any "clean up unused pages" pass — it is not.

Same for the Universal Link pair:

| Path | Purpose |
|---|---|
| `.well-known/apple-app-site-association` | Declares `X2JL72P5XT.com.tjnai.tovael` owns `/auth/confirm*`. Makes Supabase confirmation emails open the Tovael app directly. |
| `auth/confirm/index.html` | Browser fallback for the same link — reads `?code=` and hands off to `tovael://auth/confirm?code=…`. |

Tovael sets `emailRedirectTo: 'https://tjnai.ai/auth/confirm'`
(`Pool-Chemical-App/hooks/useAuth.ts`) and declares `applinks:tjnai.ai` in its
`app.json`. If the AASA file stops being served as-is, signup email
confirmation degrades to the web fallback for every Tovael user.

## Architecture

- **Static HTML only.** No npm, no bundler, no generator, no package manifest.
  Each page is a single self-contained `.html` file with one inline `<style>`
  and one inline `<script>`.
- **Deploy = push to `main`.** `.github/workflows/deploy.yml` runs on every push
  to `main` (plus `workflow_dispatch`), uploads the whole repo root
  (`path: '.'`) as the Pages artifact, and calls `actions/deploy-pages`. Pages
  is configured with `build_type: workflow`, custom domain `tjnai.ai`, HTTPS
  enforced. Typical run: 20 s – 2 min.
- **There is no `CNAME` file in the repo** — the custom domain lives in the
  repo's Pages settings. Don't "restore" one.
- **The whole repo is public web content.** Because the artifact is the repo
  root, every tracked file is reachable at `tjnai.ai/<path>` — including this
  file and `memory/`. Never commit anything you wouldn't publish.
- **One secret, injected at deploy.** `index.html` ships the literal string
  `__EL_API_KEY__`; the workflow `sed`s in `secrets.EL_API_KEY` (ElevenLabs, for
  Tianna's voice line). Keep the placeholder in the committed file. Note the key
  is still client-visible on the deployed page — it is a browser-side fetch, not
  a proxy.

## Layout

```
index.html                              tjnai.ai — landing page (hero, services,
                                        Tianna, about, contact) + matrix overlay
                                        + ElevenLabs voice
tianna-intro.html                       standalone matrix-rain → face reveal;
                                        forwards to the gallery
tianna-gallery-v4.html                  Tianna gallery, base64 images (~660 KB)
privacy.html                            site-wide TJNAI.ai LLC privacy policy
                                        (linked from the index footer)
tovael-privacy.html                     Tovael app privacy policy — App Store
                                        dependency, see above
auth/confirm/index.html                 Tovael email-confirm deep-link fallback
.well-known/apple-app-site-association  Tovael Universal Links
.nojekyll                               disables Jekyll — required, see gotchas
assets/logo/                            favicon, apple-touch-icon, og-card,
                                        neon wordmark + disc marks
.github/workflows/deploy.yml            the only CI; the deploy
memory/                                 stale pre-2026 notes, do not trust
```

**New pages go at the repo root as one self-contained `.html` file; new binary
assets go under `assets/`.** No `src/`, no `dist/`, no shared stylesheet —
whatever you add is deployed verbatim, so keep the tree flat and legible.

## Local preview

There is no build and no test suite. Serve the directory and open it:

```sh
cd ~/Documents/TJNAI/TJNAI-landing
python3 -m http.server 8000        # then http://127.0.0.1:8000/
```

Deploy state and history, when you need them:

```sh
gh api repos/scratchcat69/TJNAI-landing/pages   # domain, build_type, https
gh run list -R scratchcat69/TJNAI-landing -L 5  # recent deploys
```

## Conventions

- **Single-file pages.** Inline `<style>` + `<script>`; no external JS, no
  frameworks. Only outbound requests are Google Fonts and the ElevenLabs API.
- **Design tokens are duplicated per page** as `:root` custom properties
  (`index.html`, `tovael-privacy.html`, `privacy.html`,
  `tianna-gallery-v4.html` each declare their own). That's the pattern —
  copy the block into a new page rather than inventing a shared stylesheet.
- **Palette:** `--void #020209`, `--cyan #00d4ff`, `--purple #8b5cf6`,
  `--pink #ec4899`, text `#f1f5f9` / `#94a3b8` / `#64748b`. Dark only, no
  light mode anywhere.
- **Type:** Space Grotesk (display, `--font-d`) + Inter (body, `--font-b`),
  loaded from `fonts.googleapis.com`.
- The July 2026 "Light-the-AI" neon identity (animated tube→fill wordmark
  ignition, glow-disc mark, og/twitter cards) lives in **`index.html` only**.
  `tianna-intro.html` and `tianna-gallery-v4.html` still carry the earlier
  glassmorphism V4 look. Both are live and linked; the mismatch is known, not a
  bug to auto-fix.
- Animations respect `prefers-reduced-motion` — keep that when adding motion.
- The legal entity is **TJNAI.ai LLC** sitewide. Contact addresses in use:
  `info@tjnai.ai` (site) and `support@tjnai.ai` (Tovael policy).
- Work happens on `main` or a short feature branch merged back into it.

## Gotchas that have cost real time

- **`/tovael-privacy` (extensionless) only works on GitHub Pages.** Verified:
  a local `python3 -m http.server` returns 404 for `/tovael-privacy` and 200 for
  `/tovael-privacy.html`. Test the `.html` path locally and don't conclude the
  live URL is broken.
- **`.nojekyll` is load-bearing.** Without it, Jekyll strips dot-directories
  from the build and `/.well-known/apple-app-site-association` 404s, silently
  killing Tovael's Universal Links. Never delete it.
- **The AASA file has no extension and no `Content-Type` control.** Leave the
  filename exactly as-is; Apple fetches that literal path.
- **Anything committed here is published.** `CLAUDE.md`, `memory/*.md`, and the
  tracked `.DS_Store` files are all served from tjnai.ai.
- **A real ElevenLabs key was committed in the initial commit (`faf2330`) and
  is still in public git history** — it was replaced with the `__EL_API_KEY__`
  placeholder in `908b82d`, but history is history. Treat that key as burned;
  never re-commit a live one.
- **`memory/` is stale by years of project churn.** It still describes DipCue
  (now Tovael, shipped) and Live Groove Finder (decommissioned) as active work,
  and lists a go-live checklist for a site that has been live since spring 2026.
  Left in place deliberately — do not cite it, do not use it as input.
- **`~/Documents/TJNAI/Logo/tjnai-landing-v3.html` is NOT this repo's
  `index.html`.** It's an earlier design iteration (different DOM ids
  `particles-canvas` / `navbar` / `main-content`, links a `tianna-gallery.html`
  that doesn't exist here, ~41 lines in common with the live page). Editing it
  changes nothing on tjnai.ai. Same for `Logo/tjnai-landing-v2.html` and
  `Logo/tianna-gallery.html`.
- **`assets/logo/*` are resized derivatives**, not byte-identical copies, of the
  renders in `Logo/round2/` and `Logo/round3-compact/`. Regenerate from those
  sources rather than upscaling what's in the repo.
- Editing legal copy in `tovael-privacy.html` without matching the in-app policy
  screen (`Pool-Chemical-App/app/legal/privacy-policy.tsx`) creates a compliance
  mismatch. The two must say the same thing; the app repo is the source of truth
  for what Tovael actually does with data.

## Where things live (don't duplicate state here)

- **History, decisions, work logs, current status** → Obsidian:
  `~/Documents/TJNAI/Obsidian/TJNAI/TJNAI Agency/`. Read on demand. Status and
  in-flight work do **not** belong in this file — that's exactly how the
  previous version of it rotted.
- **What the Tovael pages must say** → the app repo,
  `~/Documents/TJNAI/Claude_Code/Pool-Chemical-App/` (`app.json` for the
  associated domain, `hooks/useAuth.ts` for the redirect URL,
  `app/legal/` for policy text).
- **Brand and logo sources** → `~/Documents/TJNAI/Logo/` (rounds of renders;
  earlier landing-page drafts also live there — see gotchas).
- **Live deploy state** → GitHub Pages settings + Actions runs, via the `gh`
  commands above. Not mirrored here.
