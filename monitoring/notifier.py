"""Deterministic operational alert delivery for Project CHF (NO LLM).

The notifier turns monitoring reports into alerts and delivers them over two
optional channels:

  * SMTP email  -- config from env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
                   SMTP_FROM, SMTP_TO. Degrades to logged-only if unset.
  * Generic webhook -- JSON POST to env WEBHOOK_URL (Slack / Discord /
                   Mattermost / Telegram-bot-proxy compatible). Degrades to
                   logged-only if unset.

Design rules:
  * Deterministic; contains no model / LLM calls.
  * NEVER crashes the caller. Any delivery error is logged and swallowed so a
    monitoring alert can never take down the scheduler.
  * If no channel is configured, the alert is logged clearly.

Config file: configs/notifications.yaml (secrets stay in env, never in yaml).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chf.notifier")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_RELPATH = "configs/notifications.yaml"

# severity ordering used for min_severity filtering and report->severity mapping
SEVERITY_ORDER: Dict[str, int] = {"info": 0, "warn": 1, "critical": 2}
# monitoring report statuses ("pass"/"warn"/"fail") mapped to alert severities
STATUS_TO_SEVERITY: Dict[str, str] = {
    "pass": "info",
    "ok": "info",
    "warn": "warn",
    "warning": "warn",
    "fail": "critical",
    "critical": "critical",
    "error": "critical",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "notifications": {
        "enabled": True,
        "min_severity": "warn",
        "subject_prefix": "[CHF]",
        "email": {"enabled": True, "use_starttls": True, "timeout_seconds": 20},
        "webhook": {"enabled": True, "timeout_seconds": 20},
        "dedup_ttl_hours": 12,
    }
}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_config(root: Optional[Path] = None, config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configs/notifications.yaml merged over built-in defaults."""
    root = root or ROOT
    path = Path(config_path) if config_path else root / DEFAULT_CONFIG_RELPATH
    if not path.is_absolute():
        path = root / path
    cfg = {"notifications": dict(DEFAULT_CONFIG["notifications"])}
    if path.exists():
        try:
            with open(path, "r") as f:
                loaded = yaml.safe_load(f) or {}
            user = loaded.get("notifications", {}) or {}
            merged = dict(cfg["notifications"])
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            cfg["notifications"] = merged
        except Exception as exc:  # noqa: BLE001 - bad config must not crash alerting
            logger.warning("notifier: failed to read %s (%s); using defaults", path, exc)
    return cfg


# ---------------------------------------------------------------------------
# channel configuration resolved from environment
# ---------------------------------------------------------------------------
@dataclass
class EmailConfig:
    host: Optional[str] = None
    port: int = 587
    user: Optional[str] = None
    password: Optional[str] = None
    sender: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    use_starttls: bool = True
    timeout: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.host and self.recipients)


@dataclass
class WebhookConfig:
    url: Optional[str] = None
    timeout: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.url)


def _env(*names: str) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def email_config_from_env(cfg: Dict[str, Any]) -> EmailConfig:
    ncfg = cfg["notifications"].get("email", {})
    to_raw = _env("SMTP_TO") or ""
    recipients = [addr.strip() for addr in to_raw.replace(";", ",").split(",") if addr.strip()]
    port_raw = _env("SMTP_PORT")
    try:
        port = int(port_raw) if port_raw else 587
    except ValueError:
        port = 587
    user = _env("SMTP_USER")
    return EmailConfig(
        host=_env("SMTP_HOST"),
        port=port,
        user=user,
        password=_env("SMTP_PASS", "SMTP_PASSWORD"),
        sender=_env("SMTP_FROM") or user,
        recipients=recipients,
        use_starttls=bool(ncfg.get("use_starttls", True)),
        timeout=int(ncfg.get("timeout_seconds", 20)),
    )


def webhook_config_from_env(cfg: Dict[str, Any]) -> WebhookConfig:
    ncfg = cfg["notifications"].get("webhook", {})
    return WebhookConfig(
        url=_env("WEBHOOK_URL"),
        timeout=int(ncfg.get("timeout_seconds", 20)),
    )


# ---------------------------------------------------------------------------
# report -> message templating (plain text + markdown)
# ---------------------------------------------------------------------------
def severity_from_status(status: Optional[str]) -> str:
    if not status:
        return "info"
    return STATUS_TO_SEVERITY.get(str(status).lower(), "warn")


def _fmt_checks(report: Dict[str, Any], only_bad: bool = True, limit: int = 25) -> List[str]:
    checks = report.get("checks") or []
    lines: List[str] = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        cstatus = str(c.get("status", "")).lower()
        if only_bad and cstatus in ("pass", "ok", ""):
            continue
        name = c.get("name", "?")
        detail = c.get("detail", "")
        val = c.get("value")
        thr = c.get("threshold")
        piece = f"[{cstatus or '?':>4}] {name}"
        if detail:
            piece += f": {detail}"
        elif val is not None:
            piece += f": value={val} threshold={thr}"
        lines.append(piece)
        if len(lines) >= limit:
            lines.append(f"... (+{len(checks) - limit} more)")
            break
    return lines


def build_subject(report: Dict[str, Any], monitor: Optional[str] = None, prefix: str = "[CHF]") -> str:
    """Deterministic subject line from a monitoring report dict."""
    status = str(report.get("status", "unknown")).upper()
    name = monitor or report.get("monitor") or report.get("name") or "monitor"
    return f"{prefix} {name}: {status}".strip()


def build_text_message(report: Dict[str, Any], monitor: Optional[str] = None) -> str:
    """Deterministic plain-text body from a monitoring report dict."""
    name = monitor or report.get("monitor") or report.get("name") or "monitor"
    status = str(report.get("status", "unknown")).upper()
    ts = report.get("as_of_utc") or report.get("generated_utc") or ""
    lines = [f"Monitor: {name}", f"Status: {status}"]
    if ts:
        lines.append(f"As of: {ts}")
    if report.get("n_checks") is not None:
        lines.append(f"Checks: {report.get('n_checks')}")
    if report.get("summary"):
        lines.append(f"Summary: {report.get('summary')}")
    bad = _fmt_checks(report, only_bad=True)
    if bad:
        lines.append("")
        lines.append("Triggered checks:")
        lines.extend(f"  {line}" for line in bad)
    return "\n".join(lines)


def build_markdown_message(report: Dict[str, Any], monitor: Optional[str] = None) -> str:
    """Deterministic markdown body (for Slack/Discord/Mattermost webhooks)."""
    name = monitor or report.get("monitor") or report.get("name") or "monitor"
    status = str(report.get("status", "unknown")).upper()
    ts = report.get("as_of_utc") or report.get("generated_utc") or ""
    header = f"*{name}* — `{status}`"
    if ts:
        header += f"  _( {ts} )_"
    parts = [header]
    if report.get("summary"):
        parts.append(str(report.get("summary")))
    bad = _fmt_checks(report, only_bad=True)
    if bad:
        parts.append("\n".join(f"- {line}" for line in bad))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------
def _send_email(subject: str, body: str, ecfg: EmailConfig) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ecfg.sender or (ecfg.user or "chf@localhost")
    msg["To"] = ", ".join(ecfg.recipients)
    msg.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP(ecfg.host, ecfg.port, timeout=ecfg.timeout) as server:
        server.ehlo()
        if ecfg.use_starttls:
            try:
                server.starttls(context=context)
                server.ehlo()
            except smtplib.SMTPException:
                logger.warning("notifier: STARTTLS not available on %s:%s", ecfg.host, ecfg.port)
        if ecfg.user and ecfg.password:
            server.login(ecfg.user, ecfg.password)
        server.send_message(msg)
    return True


def _webhook_payload(subject: str, markdown: str, text: str, severity: str) -> Dict[str, Any]:
    """One payload that satisfies Slack, Discord, Mattermost and generic hooks."""
    color = {"info": "#36a64f", "warn": "#e0a800", "critical": "#d00000"}.get(severity, "#808080")
    combined = f"{subject}\n{markdown}"
    return {
        "text": combined,          # Slack / Mattermost / generic
        "content": combined,       # Discord
        "username": "CHF Monitor",
        "severity": severity,
        "attachments": [
            {
                "color": color,
                "title": subject,
                "text": markdown,
                "fallback": text,
            }
        ],
    }


def _send_webhook(payload: Dict[str, Any], wcfg: WebhookConfig) -> bool:
    import requests  # local import so importing the module never requires requests

    resp = requests.post(wcfg.url, json=payload, timeout=wcfg.timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"webhook returned HTTP {resp.status_code}: {resp.text[:200]}")
    return True


def send(
    subject: str,
    body: str,
    severity: str = "warn",
    *,
    markdown: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Deliver an alert over every configured channel. Never raises.

    Returns a result dict: {"delivered": [...], "logged_only": bool,
    "errors": [...], "skipped": bool}. Unconfigured channels are skipped and
    the alert is always logged.
    """
    cfg = config or load_config(root=root)
    ncfg = cfg["notifications"]
    result: Dict[str, Any] = {"delivered": [], "errors": [], "logged_only": False, "skipped": False}

    severity = severity if severity in SEVERITY_ORDER else "warn"
    min_sev = str(ncfg.get("min_severity", "warn")).lower()
    min_rank = SEVERITY_ORDER.get(min_sev, 1)

    # Always log the alert (this is the guaranteed, never-crash channel).
    log_line = f"ALERT[{severity}] {subject} :: {body.splitlines()[0] if body else ''}"
    if severity == "critical":
        logger.error(log_line)
    elif severity == "warn":
        logger.warning(log_line)
    else:
        logger.info(log_line)

    if not ncfg.get("enabled", True):
        logger.info("notifier: notifications disabled in config; logged only")
        result["logged_only"] = True
        result["skipped"] = True
        return result

    if SEVERITY_ORDER[severity] < min_rank:
        logger.info("notifier: severity %s below min_severity %s; logged only", severity, min_sev)
        result["logged_only"] = True
        result["skipped"] = True
        return result

    markdown = markdown if markdown is not None else body

    # email
    ecfg = email_config_from_env(cfg)
    if ncfg.get("email", {}).get("enabled", True) and ecfg.configured:
        try:
            _send_email(subject, body, ecfg)
            result["delivered"].append("email")
            logger.info("notifier: email delivered to %s", ", ".join(ecfg.recipients))
        except Exception as exc:  # noqa: BLE001 - delivery failure must not crash caller
            result["errors"].append(f"email: {exc}")
            logger.error("notifier: email delivery failed: %s", exc)
    else:
        logger.info("notifier: email channel not configured (set SMTP_HOST/SMTP_TO); skipped")

    # webhook
    wcfg = webhook_config_from_env(cfg)
    if ncfg.get("webhook", {}).get("enabled", True) and wcfg.configured:
        try:
            payload = _webhook_payload(subject, markdown, body, severity)
            _send_webhook(payload, wcfg)
            result["delivered"].append("webhook")
            logger.info("notifier: webhook delivered")
        except Exception as exc:  # noqa: BLE001 - delivery failure must not crash caller
            result["errors"].append(f"webhook: {exc}")
            logger.error("notifier: webhook delivery failed: %s", exc)
    else:
        logger.info("notifier: webhook channel not configured (set WEBHOOK_URL); skipped")

    if not result["delivered"]:
        result["logged_only"] = True
    return result


def notify_report(
    report: Dict[str, Any],
    monitor: Optional[str] = None,
    *,
    config: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Convenience: build subject/body from a monitoring report and send()."""
    cfg = config or load_config(root=root)
    prefix = str(cfg["notifications"].get("subject_prefix", "[CHF]"))
    severity = severity_from_status(report.get("status"))
    subject = build_subject(report, monitor=monitor, prefix=prefix)
    body = build_text_message(report, monitor=monitor)
    md = build_markdown_message(report, monitor=monitor)
    return send(subject, body, severity, markdown=md, config=cfg, root=root)


def _demo() -> int:
    sample = {
        "monitor": "data_quality",
        "status": "fail",
        "as_of_utc": "2026-07-06T05:45:00+00:00",
        "n_checks": 3,
        "checks": [
            {"name": "market_staleness", "status": "fail", "value": 5, "threshold": 3, "detail": "max date 5d old"},
            {"name": "market_nan_rate_close", "status": "warn", "value": 0.02, "threshold": 0.01, "detail": "NaN share of close"},
            {"name": "coverage", "status": "pass", "value": 0.99, "threshold": 0.9, "detail": "ok"},
        ],
    }
    print("=== subject ===")
    print(build_subject(sample))
    print("=== text body ===")
    print(build_text_message(sample))
    print("=== markdown body ===")
    print(build_markdown_message(sample))
    print("=== send() (no channels configured -> logged only) ===")
    res = notify_report(sample, monitor="data_quality")
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
