# fda-scrape-proxy (temporary)

A throwaway tinyproxy instance on fly.dev, used only to test whether routing
FDA scraper traffic through a fly.io egress IP gets past the Akamai block
that GitHub Actions runner IPs hit. **Not meant to run long-term** — destroy
the app once the test is done (see bottom).

## 1. Log in to fly (interactive, run yourself)

```
fly auth login
```

## 2. Create the app

```
cd fly-proxy
fly apps create fda-scrape-proxy
```

If that name is taken, edit `app =` in `fly.toml` and reuse it below.

## 3. Set proxy credentials

```
fly secrets set \
  BASIC_AUTH_USER=$(openssl rand -hex 8) \
  BASIC_AUTH_PASS=$(openssl rand -hex 20) \
  --app fda-scrape-proxy
```

Fetch what got set (fly doesn't echo secret values back) by generating and
saving them locally before running the command instead, e.g.:

```
USER=$(openssl rand -hex 8)
PASS=$(openssl rand -hex 20)
echo "user=$USER pass=$PASS"   # save these somewhere for step 5
fly secrets set BASIC_AUTH_USER="$USER" BASIC_AUTH_PASS="$PASS" --app fda-scrape-proxy
```

## 4. Deploy

```
fly deploy --app fda-scrape-proxy
```

## 5. Test the proxy can reach FDA

From any machine (your laptop, or temporarily from a GitHub Actions run):

```
curl -v \
  --proxy "http://$USER:$PASS@fda-scrape-proxy.fly.dev:8888" \
  https://www.fda.gov/food/outbreaks-foodborne-illness/public-health-advisories-investigations-foodborne-illness-outbreaks
```

Or with Python requests (matches how `main.py`'s `requests.Session` would use it):

```python
import requests
proxies = {
    "https": f"http://{user}:{pass_}@fda-scrape-proxy.fly.dev:8888",
}
r = requests.get("https://www.fda.gov/food/outbreaks-foodborne-illness/public-health-advisories-investigations-foodborne-illness-outbreaks", proxies=proxies, timeout=30)
print(r.status_code)
```

To test from an actual GitHub Actions runner IP (the real question we're
answering), add a throwaway step to a workflow run that does the above curl
against `${{ secrets.PROXY_USER }}` / `${{ secrets.PROXY_PASS }}`, or run it
manually via `workflow_dispatch` — do **not** commit real proxy credentials
into the repo or workflow file.

## 6. Tear down when done

```
fly apps destroy fda-scrape-proxy
```

This deletes the app and its machine entirely (not just stops it) — there's
nothing else to clean up.

## Notes

- The client-to-proxy leg (`fda-scrape-proxy.fly.dev:8888`) is plain TCP,
  not TLS — tinyproxy doesn't terminate TLS itself, and `fly.toml` uses a
  raw TCP passthrough (not `http_service`) because forward-proxy `CONNECT`
  tunneling doesn't work through fly's L7 HTTP routing. The proxy-to-FDA leg
  is still a normal end-to-end HTTPS connection (the tunnel just carries the
  client's own TLS bytes) — only the Basic Auth credentials on the first leg
  are sent in the clear. That's why credentials are random, single-purpose,
  and the app gets destroyed right after the test.
- `min_machines_running = 0` + `auto_stop_machines = "stop"` means the
  machine also suspends itself after ~5 min idle even if you forget step 6,
  but destroying the app is the real cleanup — a stopped machine still
  exists and could theoretically be started again.
