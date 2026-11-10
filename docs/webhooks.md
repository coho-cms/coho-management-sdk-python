# Webhooks

Endpoints Coho calls when something happens in a project (coho-data doc 70): a promotion, a
tag ready, a branch merged, entries changed, a token made. Each request is signed as
[Standard Webhooks](https://www.standardwebhooks.com) signs one, with the endpoint's own
secret. Owners only; a project token never is one, so it cannot manage them. A project has
at most 25.

```python
site = coho.account("acme").project(PROJECT)
```

## Endpoints

```python
site.webhooks.events()                    # WebhookEvents: .events, and .groups such as "tag.*"

created = site.webhooks.create(
    url="https://build.example.com/hooks/coho",       # HTTPS, on a public address
    events=["environment.promoted", "tag.*"],         # types, groups, or "*"
    refs=["prod"],                                    # ref patterns, * and ?; None: every ref
    headers={"Authorization": "Bearer …"},            # sent with each request, never read back
    description="Rebuild the site",
    enabled=True,
)
created.secret                            # ⚠️ whsec_…, in this response and nowhere else
w = created.webhook                       # Webhook

site.webhooks.list()                      # WebhookList: .webhooks, each with .host, and .limit
site.webhooks.get(w.id).url               # the full URL, on one webhook's read only
w.health                                  # last_state, last_at, succeeded_24h, failed_24h
w.failing_since, w.disabled_reason        # set while it fails, and once it has been disabled
```

`update` changes what it is given and leaves the rest. `refs=[]` is every ref again.
`headers` replaces every custom header, since their values cannot be read back to merge
with; `headers={}` clears them. `description=""` clears the description, and `enabled=True`
also clears the run of failures that disabled it.

```python
site.webhooks.update(w.id, events=["*"], refs=[])
site.webhooks.update(w.id, enabled=False)
site.webhooks.delete(w.id)                # with its deliveries and their log
```

## GitHub Actions

A webhook can start a GitHub Actions workflow directly, through GitHub's
`repository_dispatch` API, with no relay in between. Give it a `repository` and a `token` in
place of `url` and `headers`; the kind follows from the repository, and is fixed once made.

```python
created = site.webhooks.create(
    repository="acme/site",                           # owner/name
    token="github_pat_…",                             # can write to the repository's contents
    events=["environment.promoted"],                  # a rollback is a promotion too
    refs=["prod"],
)
created.secret                            # None: GitHub checks the token, and nothing is signed
w = created.webhook
w.kind, w.repository                      # "github_dispatch", "acme/site"; w.host is api.github.com

site.webhooks.update(w.id, token="github_pat_…")   # how its credential is rotated
```

Each event arrives as a `repository_dispatch` whose `event_type` is the event's type and
whose `client_payload` is the whole event, so a workflow picks events with
`on.repository_dispatch.types` and reads `github.event.client_payload.data`. `rotate_secret`
refuses one, with `WebhookInvalid`, since it has no secret; so does giving it a `url` or
`headers`, or giving a standard webhook a `repository` or `token`.

## Secrets

```python
rotated = site.webhooks.rotate_secret(w.id)
rotated.secret                            # ⚠️ shown once
rotated.previous_secret_until             # the old one signs beside it until then, 24 hours
```

For that day every request carries two signatures, one per secret, so a receiver can be
updated at its own pace. Rotating again within the day erases the oldest at once, which is
how a leaked secret is answered: rotate twice.

## The log

```python
delivery_id = site.webhooks.test(w.id)    # a ping, queued; the sender sends it within seconds
d = site.webhooks.delivery(w.id, delivery_id)
d.delivery.state                          # held, pending, running, succeeded, or failed
d.body                                    # exactly what was signed
for a in d.attempts:
    a.n, a.started_at, a.duration_ms, a.response_status, a.error_kind, a.error, a.response_body

log = site.webhooks.deliveries(w.id, state="failed", type="entries.changed",
                               since="2026-11-01T00:00:00Z", limit=100)    # newest first
older = site.webhooks.deliveries(w.id, before=log.deliveries[-1].created_at)   # the next page

site.webhooks.redeliver(w.id, delivery_id)              # a finished one, with the same event id
site.webhooks.redeliver_failed(w.id, "2026-11-02T09:00:00Z")   # every failure since; how many
```

`since` and `before` take ISO 8601 or a timezone-aware `datetime`. The error kinds are
`dns`, `refused_address`, `connect`, `tls`, `timeout`, `redirect`, `http`, and `internal`.
A redelivery keeps the event id (the `webhook-id` header), so a receiver that handled it
already can drop it.

## Refusals

| Class | `code` | |
|---|---|---|
| `WebhookInvalid` | `WEBHOOK_INVALID` | no events, too many patterns, a test sent to a disabled one; the parent of the four below |
| ↳ `WebhookUrlInvalid` | `WEBHOOK_URL_INVALID` | not HTTPS, or a user name or password in it |
| ↳ `WebhookUrlRefused` | `WEBHOOK_URL_REFUSED` | it is, or resolves to, a private, loopback, or link-local address |
| ↳ `WebhookEventUnknown` | `WEBHOOK_EVENT_UNKNOWN` | not one of `events()` |
| ↳ `WebhookHeaderReserved` | `WEBHOOK_HEADER_RESERVED` | a header Coho sets itself |
| `WebhookLimitReached` | `WEBHOOK_LIMIT_REACHED` | 25 already; delete one first |
| `WebhooksUnconfigured` | `WEBHOOKS_UNCONFIGURED` | 503: the deployment has no key to seal secrets with |

A change on an archived project is `PROJECT_ARCHIVED`, a plain `CohoError`; its log can still
be read.
