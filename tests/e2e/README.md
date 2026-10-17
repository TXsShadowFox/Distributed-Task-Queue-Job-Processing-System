# End-to-end tests (Playwright)

The suite drives the real dashboard in a browser against a running FlowForge stack: API, at least one worker, the
scheduler and the web app. Each test registers its own organization through the API, so tests are independent of
each other and of any seed data.

```bash
cd tests/e2e
npm ci
npx playwright install chromium            # not needed on Windows, which uses the installed Microsoft Edge
npx playwright test --project=desktop --project=mobile
npx playwright test --project=screenshots  # README screenshots; needs the demo data (scripts/seed_demo.py)
```

| Variable | Default | Meaning |
|---|---|---|
| `FLOWFORGE_WEB_URL` | `http://localhost:3000` | The dashboard |
| `FLOWFORGE_API_URL` | `http://127.0.0.1:8000` | The API, used for test setup. Behind nginx, use the same URL as the dashboard |
| `PW_CHANNEL` | `msedge` on Windows | Browser channel. Windows Smart App Control can block the unsigned browsers Playwright downloads, so the signed, installed Edge is used there |
| `FLOWFORGE_DEMO_EMAIL`, `FLOWFORGE_DEMO_PASSWORD` | demo account | Account used by the screenshot capture |

**Rate limits.** Every full page load restores the session through `/auth/refresh`, which shares the per-IP auth
rate limit with login and register (30 per minute by default). The specs navigate inside the app where they can,
but the suite as a whole still needs a higher limit. Start the API with, for example,
`FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE=600` (CI sets 1,000).

**Projects.** `desktop` (1440×900) runs every functional spec, `mobile` (Pixel 7) runs `responsive.spec.ts`, and
`screenshots` writes `docs/screenshots/*.png`.

**Selectors.** With `cacheComponents`, Next.js keeps the previous page mounted but hidden, so prefer role-based
locators (`getByRole`), which ignore hidden elements, over `getByLabel` and `getByText`.

`tools/peek.mjs` is a small helper for manual checks: it signs in, screenshots the given routes and prints any
console errors or warnings.
