# fda-scrape-proxy

A tinyproxy instance on fly.dev that the nightly workflow
(`.github/workflows/scrape-outbreaks.yml`) routes FDA scraper traffic
through, to work around FDA's Akamai protection blocking GitHub Actions
runner IPs.

## Why this exists

FDA (`www.fda.gov`) sits behind Akamai. Requests from some GitHub Actions
runner IPs get redirected to an Akamai "abuse detection" page instead of the
real content — confirmed with both `requests` and a real headless Chromium
(Playwright), so it isn't a bot-fingerprint check, it's IP-based. GitHub
Actions runner IPs are drawn from a large shared pool, and apparently only
part of that pool is on Akamai's blocklist at any given time, which is why
results were inconsistent between test runs (a single `curl` from one runner
IP succeeded; a full scrape run from a different runner IP failed).

Routing through this fixed fly.dev IP avoids that flakiness: it's a stable,
known-good egress point instead of a lottery over GitHub's IP pool.

CDC does not need this — CDC's Akamai protection is a bot/fingerprint check
that a real headless browser passes regardless of source IP, which is why
`CDCOutbreakScraper` in `main.py` already worked with just a Playwright
switch and no proxy.

## How it's wired up

- `main.py`'s `FDAOutbreakScraper` launches its Playwright browser with a
  proxy when the env vars `FDA_PROXY_SERVER` (and optionally
  `FDA_PROXY_USERNAME` / `FDA_PROXY_PASSWORD`) are set. Unset locally — FDA
  has not been observed to block non-GitHub-Actions IPs.
- The workflow sets those env vars from repo secrets `FDA_PROXY_USERNAME` /
  `FDA_PROXY_PASSWORD`, pointing at `fda-scrape-proxy.fly.dev:8888`.
- `min_machines_running = 0` + `auto_stop_machines = "stop"` in `fly.toml`
  means the machine suspends itself when idle and wakes on the next
  connection — cost is close to zero beyond the ~$2/mo dedicated IPv4. Left
  alone, fly's own idle timeout takes ~5 minutes to kick in (confirmed by
  observation; not separately configurable for a `[[services]]`-based TCP
  service the way `http_idle_timeout` is for `http_service`).
- The workflow's "Shut down fly.dev proxy" step (after the FDA scrape step,
  `if: always()`) stops the machine explicitly right after use instead of
  waiting out that idle timeout, using a fly.io deploy token scoped to just
  this app (`FLY_PROXY_API_TOKEN` repo secret). If that step is ever
  removed, the machine still stops on its own via `auto_stop_machines` — the
  explicit stop is a nicety, not required for correctness.
- Deliberately no `services.tcp_checks` health check in `fly.toml`: each
  health-check probe counts as activity, which kept the machine in
  "started" indefinitely and defeated `auto_stop_machines` entirely (this
  was tested and observed directly — don't re-add one without checking this
  interaction again).

## Redeploying after a change to this directory

```
cd fly-proxy
fly deploy --app fda-scrape-proxy --remote-only
```

(There's no auto-deploy workflow for this yet, unlike `ai-art`'s
`fly-image-store` — add one with a `FLY_API_TOKEN` repo secret if this needs
to change often.)

## First-time setup (already done for `fda-scrape-proxy`)

```
fly apps create fda-scrape-proxy
fly secrets set BASIC_AUTH_USER=$(openssl rand -hex 8) BASIC_AUTH_PASS=$(openssl rand -hex 20) --app fda-scrape-proxy
fly ips allocate-v4 --app fda-scrape-proxy   # DEDICATED ip4, not --shared
fly deploy --app fda-scrape-proxy --remote-only
```

A **dedicated** IPv4 is required — fly.dev's shared IPv4s only route
HTTP(S)/SNI traffic, not the raw TCP `CONNECT`-tunnel pattern a forward
proxy uses; a shared IP causes every proxied connection to reset immediately
after the `CONNECT` request is sent.

The same `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` values used in `fly secrets
set` above must also be set as the `FDA_PROXY_USERNAME` / `FDA_PROXY_PASSWORD`
GitHub Actions repo secrets.

## Rotating credentials

```
USER=$(openssl rand -hex 8)
PASS=$(openssl rand -hex 20)
fly secrets set BASIC_AUTH_USER="$USER" BASIC_AUTH_PASS="$PASS" --app fda-scrape-proxy
gh secret set FDA_PROXY_USERNAME --body "$USER"
gh secret set FDA_PROXY_PASSWORD --body "$PASS"
```

## Tearing down

If FDA scraping is ever dropped or this stops being needed:

```
fly apps destroy fda-scrape-proxy
```

Also remove the `FDA_PROXY_USERNAME` / `FDA_PROXY_PASSWORD` repo secrets and
the `env:` block on the FDA step in `scrape-outbreaks.yml` (the scraper
falls back to a direct, unproxied connection when those env vars are unset).
