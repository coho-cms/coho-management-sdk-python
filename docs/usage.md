# Usage

What a project used, and what the account used across its projects (coho-data doc 41),
metered hourly in UTC. The current hour is still filling.

```python
site = coho.account("acme").project(project_id)
report = site.usage()                          # the last day, hour by hour
report = site.usage(grain="day")               # the last 30 days, day by day
report = site.usage(start="2026-10-01T00:00:00Z", end=datetime.now(UTC), grain="day")
report = site.usage(meters=["delivery.requests", "delivery.bytes"])

acme = coho.account("acme").usage(grain="day") # admin: every project summed
acme.projects                                  # [ProjectUsageTotals]: id, name, totals by meter
```

Who may read them:

| Report | Path | Readable by |
|---|---|---|
| `Project.usage()` | `…/projects/{project}/usage` | anyone who can read the project, project tokens included |
| `Account.usage()` | `…/projects/usage` | account admins: a member gets `Forbidden` (`permission: account:admin`), a project token `TOKEN_NOT_ALLOWED` |

`start` is rounded down and `end` up to whole periods. Hourly rows are kept 90 days, so
ask for `grain="day"` further back; a range the stored data cannot answer honestly is
refused `400 USAGE_QUERY_INVALID`.

## Reading a report

A `UsageReport` holds `buckets` (the start of each period) and `meters`, each with one
`UsageSeries` per combination of its dimensions, `values` lined up with `buckets`. The
account's report collapses the dimensions: one series per meter.

The helpers do the portal's arithmetic, so both show the same numbers:

```python
report.total("delivery.requests")                                 # a flow: the sum
report.total("delivery.peak_rpm")                                 # a peak: the largest period
report.total("assets.stored_bytes")                               # a stock: the latest value
report.total("delivery.requests", lambda d: d["outcome"] == "refused")
report.per_bucket("management.requests")                          # [int] per bucket
report.totals_by("management.requests", "class", lambda d: d["actor"] == "token")

for k in report.delivery_by_key():                                # busiest first
    print(k.key, k.requests, k.not_modified, k.refused, k.bytes, k.ratio)
```

`KeyUsage.ratio` is answered requests per distinct URL per period. A client that caches
asks for each URL about once per tag, and once per `max-age` for an environment: near 1.
In the tens or hundreds, it is fetching the same answer again; sending `If-None-Match`
turns a repeat into a `304`.

The meters, by name: `delivery.requests`, `.bytes`, `.billable_bytes`, `.distinct_urls`,
`.peak_rpm`; `management.requests`, `.peak_rpm`; `preview.requests`, `.bytes`;
`authoring.versions`, `.noop_versions`; `history.versions`, `.bytes`; `content.records`,
`.published_bytes`, `.published_entries`, `.index_bytes`; `refs.count`; `tokens.active`;
`assets.stored_bytes`, `.stored_count`, `.requests`, `.egress_bytes`. Each says its own
`kind` and `unit`.
