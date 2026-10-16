#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Generates the synthetic fixtures for 2026-04-22 14:00:00 → 15:00:00 UTC.

Three pages fire in that hour, but there are only two root causes:

  INC-2276  inventory p99 latency   deploy c81d4e7 (14:12:40) drops the cache
                                    on a heavy SKU lookup
  INC-2277  checkout p99 latency    deploy a3f9c21 (14:31:18) turns one batched
                                    query into an N+1
  INC-2278  cart error rate         no cart change: checkout's N+1 saturates
                                    db-primary, which cart shares

    python data/generate_log.py             # writes data/app.log (not in git)
    python data/generate_log.py --metrics   # also rewrites data/metrics.json

Deterministic (seeded) so reruns are stable. Stdlib only.
"""
import json
import random
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "app.log"
METRICS_OUT = HERE / "metrics.json"

START = datetime(2026, 4, 22, 14, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 4, 22, 15, 0, 0, tzinfo=timezone.utc)
INVENTORY_DEPLOY_TS = datetime(2026, 4, 22, 14, 12, 40, tzinfo=timezone.utc)
CHECKOUT_DEPLOY_TS = datetime(2026, 4, 22, 14, 31, 18, tzinfo=timezone.utc)
DB_SLOW_TS = datetime(2026, 4, 22, 14, 37, 0, tzinfo=timezone.utc)
POOL_EXHAUST_TS = datetime(2026, 4, 22, 14, 40, 0, tzinfo=timezone.utc)
LATE_DEPLOY_TS = datetime(2026, 4, 22, 14, 55, 40, tzinfo=timezone.utc)

SERVICES = ["checkout", "cart", "auth", "inventory"]
ENDPOINTS = {
    "checkout": ["/api/checkout/submit", "/api/checkout/summary", "/api/checkout/validate"],
    "cart": ["/api/cart", "/api/cart/add", "/api/cart/remove"],
    "auth": ["/api/auth/login", "/api/auth/refresh", "/api/auth/logout"],
    "inventory": ["/api/inventory/check", "/api/inventory/reserve"],
}
HOSTS = {svc: [f"{svc}-{i}" for i in range(1, 4)] for svc in SERVICES}

rng = random.Random(42)


def rid() -> str:
    return "req_" + "".join(rng.choices(string.hexdigits.lower(), k=12))


def uid() -> str:
    return f"u_{rng.randint(10000, 99999)}"


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def emit(buf: list, ts: datetime, **fields) -> None:
    rec = {"ts": iso(ts)}
    rec.update(fields)
    buf.append(json.dumps(rec, separators=(",", ":")))


def ms(n: int) -> timedelta:
    return timedelta(milliseconds=n)


def healthy_request(buf: list, ts: datetime, service: str) -> None:
    """A single normal request: start + end log lines."""
    req, user = rid(), uid()
    host = rng.choice(HOSTS[service])
    ep = rng.choice(ENDPOINTS[service])
    lat = rng.randint(20, 80)
    emit(buf, ts, level="INFO", service=service, host=host,
         msg=f"request_start method=POST path={ep}", request_id=req, user_id=user)
    emit(buf, ts + ms(lat), level="INFO", service=service, host=host,
         msg=f"request_end status=200 path={ep}", request_id=req, user_id=user,
         latency_ms=lat, status=200)


def inventory_request(buf: list, ts: datetime) -> None:
    """Inventory: cached before c81d4e7, one heavy DB query per request after."""
    req, user = rid(), uid()
    host = rng.choice(HOSTS["inventory"])
    ep = rng.choice(ENDPOINTS["inventory"])
    sku = rng.randint(100000, 999999)
    query = 'db_query sql="SELECT sku, SUM(qty) FROM sku_stock WHERE sku = ? GROUP BY warehouse" db=db-inventory'
    emit(buf, ts, level="INFO", service="inventory", host=host,
         msg=f"request_start method=POST path={ep}", request_id=req, user_id=user)

    if ts < INVENTORY_DEPLOY_TS:
        if rng.random() < 0.995:
            lat = rng.randint(20, 60)
            emit(buf, ts + ms(2), level="DEBUG", service="inventory", host=host,
                 msg=f"cache_hit key=sku:{sku} ttl_left_s={rng.randint(5, 299)}", request_id=req)
        else:
            dur = rng.randint(180, 420)
            lat = dur + rng.randint(10, 40)
            emit(buf, ts + ms(2), level="DEBUG", service="inventory", host=host,
                 msg=f"cache_miss key=sku:{sku}", request_id=req)
            emit(buf, ts + ms(4 + dur), level="DEBUG", service="inventory", host=host,
                 msg=query, request_id=req, duration_ms=dur)
    else:
        # No cache lines at all any more: every request goes to the database.
        dur = rng.randint(220, 560)
        lat = dur + rng.randint(15, 60)
        emit(buf, ts + ms(3 + dur), level="DEBUG", service="inventory", host=host,
             msg=query, request_id=req, duration_ms=dur)

    emit(buf, ts + ms(lat), level="INFO", service="inventory", host=host,
         msg=f"request_end status=200 path={ep}", request_id=req, user_id=user,
         latency_ms=lat, status=200)


def checkout_request(buf: list, ts: datetime) -> None:
    """A checkout request after a3f9c21: N+1 queries, slow, maybe 500."""
    req, user = rid(), uid()
    host = rng.choice(HOSTS["checkout"])
    ep = rng.choice(ENDPOINTS["checkout"])
    emit(buf, ts, level="INFO", service="checkout", host=host,
         msg=f"request_start method=POST path={ep}", request_id=req, user_id=user)

    mins_since = (ts - CHECKOUT_DEPLOY_TS).total_seconds() / 60.0
    cursor = ts
    for _ in range(rng.randint(8, 16)):
        cursor += ms(rng.randint(2, 30))
        emit(buf, cursor, level="DEBUG", service="checkout", host=host,
             msg=f'db_query sql="SELECT * FROM order_items WHERE order_id = ?" params=[{rng.randint(1000, 9999)}] db=db-primary',
             request_id=req, duration_ms=rng.randint(2, 25))

    lat = rng.randint(800, 4000) + int(mins_since * 50)
    pool_down = ts >= POOL_EXHAUST_TS
    if pool_down and rng.random() < 0.15:
        emit(buf, cursor + ms(5), level="ERROR", service="checkout", host=host,
             msg="DB connection pool exhausted (active=50/50, waiters=12) db=db-primary", request_id=req)
        emit(buf, cursor + ms(8), level="ERROR", service="checkout", host=host,
             msg=f"request_end status=500 path={ep} error=PoolTimeout",
             request_id=req, user_id=user, latency_ms=lat, status=500)
        return
    if pool_down and rng.random() < 0.5:
        emit(buf, cursor, level="WARN", service="checkout", host=host,
             msg=f"db_pool high utilization active={rng.randint(42, 50)}/50 db=db-primary", request_id=req)
    emit(buf, cursor + ms(10), level="INFO", service="checkout", host=host,
         msg=f"request_end status=200 path={ep}", request_id=req, user_id=user,
         latency_ms=lat, status=200)


def cart_request(buf: list, ts: datetime) -> None:
    """Cart ships nothing, but it shares db-primary with checkout."""
    req, user = rid(), uid()
    host = rng.choice(HOSTS["cart"])
    ep = rng.choice(ENDPOINTS["cart"])
    sql = 'sql="SELECT * FROM cart_items WHERE cart_id = ?" db=db-primary'
    emit(buf, ts, level="INFO", service="cart", host=host,
         msg=f"request_start method=POST path={ep}", request_id=req, user_id=user)

    if ts >= POOL_EXHAUST_TS and rng.random() < 0.12:
        emit(buf, ts + ms(2000), level="ERROR", service="cart", host=host,
             msg=f"db query timeout after 2000ms {sql}", request_id=req)
        emit(buf, ts + ms(2004), level="ERROR", service="cart", host=host,
             msg=f"request_end status=503 path={ep} error=DBTimeout",
             request_id=req, user_id=user, latency_ms=2004, status=503)
        return

    slow = rng.randint(900, 1900) if ts >= POOL_EXHAUST_TS else rng.randint(120, 480)
    if rng.random() < 0.45:
        emit(buf, ts + ms(slow), level="WARN", service="cart", host=host,
             msg=f"slow query {slow}ms {sql}", request_id=req, duration_ms=slow)
        lat = slow + rng.randint(10, 60)
    else:
        lat = rng.randint(60, 300)
    emit(buf, ts + ms(lat), level="INFO", service="cart", host=host,
         msg=f"request_end status=200 path={ep}", request_id=req, user_id=user,
         latency_ms=lat, status=200)


def auth_red_herring(buf: list) -> None:
    """Unrelated auth ERRORs around 14:15 that self-resolve — a red herring."""
    base = START + timedelta(minutes=15)
    for i in range(6):
        ts = base + timedelta(seconds=i * 7 + rng.randint(0, 3))
        emit(buf, ts, level="ERROR", service="auth", host=rng.choice(HOSTS["auth"]),
             msg="upstream identity-provider timeout after 5000ms", request_id=rid(),
             upstream="idp.internal")
    emit(buf, base + timedelta(seconds=55), level="WARN", service="auth",
         host="auth-1", msg="circuit_breaker OPEN for idp.internal (will retry in 30s)")
    emit(buf, base + timedelta(seconds=90), level="INFO", service="auth",
         host="auth-1", msg="circuit_breaker CLOSED for idp.internal — recovered")


def background_noise(buf: list) -> None:
    """Healthchecks, GC pauses, deploy markers — realistic chatter."""
    t = START
    while t < END:
        for svc in SERVICES:
            emit(buf, t, level="DEBUG", service=svc, host=rng.choice(HOSTS[svc]),
                 msg="healthcheck OK", uptime_s=rng.randint(3600, 86400))
        t += timedelta(seconds=10)

    for _ in range(40):
        ts = START + timedelta(seconds=rng.randint(0, 3600))
        svc = rng.choice(SERVICES)
        emit(buf, ts, level="WARN", service=svc, host=rng.choice(HOSTS[svc]),
             msg=f"GC pause {rng.randint(80, 350)}ms (G1 young gen)")

    for ts, svc, sha in [(INVENTORY_DEPLOY_TS, "inventory", "c81d4e7"),
                         (CHECKOUT_DEPLOY_TS, "checkout", "a3f9c21"),
                         (LATE_DEPLOY_TS, "inventory", "7b3e8f0")]:
        emit(buf, ts, level="INFO", service=svc, host=f"{svc}-1",
             msg=f"deploy_complete version={sha} strategy=rolling", commit=sha)


def main() -> None:
    buf: list[str] = []

    # Baseline traffic across the full hour, ~5 req/s split across services.
    t = START
    while t < END:
        for _ in range(rng.randint(4, 6)):
            ts = t + ms(rng.randint(0, 999))
            svc = rng.choices(SERVICES, weights=[3, 3, 2, 2])[0]
            if svc == "checkout" and ts >= CHECKOUT_DEPLOY_TS:
                checkout_request(buf, ts)
            elif svc == "cart" and ts >= DB_SLOW_TS:
                cart_request(buf, ts)
            elif svc == "inventory":
                inventory_request(buf, ts)
            else:
                healthy_request(buf, ts, svc)
        t += timedelta(seconds=1)

    auth_red_herring(buf)
    background_noise(buf)

    # Sort chronologically (cheap parse of fixed-width ISO prefix)
    buf.sort(key=lambda line: line[7:31])

    OUT.write_text("\n".join(buf) + "\n")
    print(f"wrote {OUT} — {len(buf):,} lines, {OUT.stat().st_size:,} bytes")


# ── metrics.json ──────────────────────────────────────────────────────────
def write_metrics() -> None:
    """Per-minute series for the same hour, consistent with the log above."""
    r = random.Random(2277)
    minutes = range(61)

    def series(fn, digits: int = 3) -> list[dict]:
        return [{"ts": (START + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "value": round(fn(m), digits)} for m in minutes]

    def ramp(m: int, start: int, steps: list[float], before, after) -> float:
        if m < start:
            return before()
        return steps[m - start] if m - start < len(steps) else after()

    u = r.uniform
    metrics = {
        "checkout": {
            "p99_latency_ms": series(lambda m: ramp(
                m, 32, [347, 852, 1058, 1489, 2136, 1980, 2640, 3299, 3640],
                lambda: u(54, 78), lambda: u(2300, 3500))),
            "error_rate": series(lambda m: u(0.08, 0.21) if m >= 40 else u(0, 0.003)),
            "db_pool_utilization": series(lambda m: ramp(
                m, 32, [0.34, 0.46, 0.62, 0.77, 0.91], lambda: u(0.10, 0.19), lambda: 1.0)),
        },
        "cart": {
            "p99_latency_ms": series(lambda m: ramp(
                m, 37, [180, 310, 470], lambda: u(40, 65), lambda: u(1950, 2060))),
            "error_rate": series(lambda m: u(0.09, 0.15) if m >= 40 else u(0.001, 0.004)),
            "db_pool_utilization": series(lambda m: ramp(
                m, 37, [0.31, 0.48, 0.66], lambda: u(0.08, 0.14), lambda: u(0.88, 1.0))),
        },
        "auth": {
            "p99_latency_ms": series(lambda m: {15: 5000.0, 16: 2400.0}.get(m) or u(28, 40)),
            "error_rate": series(lambda m: {15: 0.061, 16: 0.018}.get(m) or u(0, 0.002)),
        },
        "inventory": {
            "p99_latency_ms": series(lambda m: u(380, 620) if m >= 13 else u(30, 46)),
            "error_rate": series(lambda m: u(0.001, 0.004)),
            "cache_hit_rate": series(lambda m: 0.0 if m >= 13 else (0.62 if m == 12 else u(0.990, 0.999))),
        },
        "db-primary": {
            "queries_per_sec": series(lambda m: ramp(m, 32, [212], lambda: u(38, 46), lambda: u(430, 520)), 1),
            "cpu_utilization": series(lambda m: ramp(
                m, 32, [0.45, 0.60, 0.71, 0.80, 0.88], lambda: u(0.18, 0.24), lambda: u(0.95, 0.99))),
        },
        "db-inventory": {
            "queries_per_sec": series(lambda m: u(11, 14) if m >= 13 else u(0.8, 1.6), 1),
            "cpu_utilization": series(lambda m: u(0.35, 0.45) if m >= 13 else u(0.05, 0.08)),
        },
    }
    METRICS_OUT.write_text(json.dumps(metrics, indent=1) + "\n")
    print(f"wrote {METRICS_OUT} — {sum(len(v) for v in metrics.values())} series")


if __name__ == "__main__":
    main()
    if "--metrics" in sys.argv:
        write_metrics()
