#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch Dongchedi monthly sales and email the CSV.
Supports SMTP or SendGrid. Designed for GitHub Actions or a server cron.
"""

import os
import csv
import time
import json
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://www.dongchedi.com/motor/pc/car/rank_data"
HEADERS = {
    "User-Agent": os.environ.get("DCD_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"),
    "Referer": "https://www.dongchedi.com/sales/"
}

def month_iter(start_yyyymm: int, end_yyyymm: int):
    y, m = divmod(start_yyyymm, 100)
    ey, em = divmod(end_yyyymm, 100)
    cur = date(y, m, 1)
    to = date(ey, em, 1)
    while cur <= to:
        yield cur.strftime("%Y%m")
        # handle year rollover
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

def _fetch_page(yyyymm: str, offset: int, page_size: int, new_energy_type):
    params = {
        "aid": "1839",
        "app_name": "auto_web_pc",
        "rank_data_type": "11",
        "month": int(yyyymm),
        "count": page_size,
        "offset": offset,
    }
    if new_energy_type is not None:
        params["new_energy_type"] = new_energy_type

    # simple retry/backoff
    backoff = 1.0
    for attempt in range(6):
        try:
            r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 5:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 1.8, 8.0)

def fetch_month_all(yyyymm: str, page_size=100, new_energy_type=None, max_pages=100):
    """Fetch all rows for a given month with automatic pagination and de-dup."""
    rows, offset = [], 0
    seen_ids = set()
    for _ in range(max_pages):
        js = _fetch_page(yyyymm, offset, page_size, new_energy_type)
        data = js.get("data") or {}
        items = data.get("list") or []
        total = data.get("total")
        has_more = data.get("has_more")

        added = 0
        for it in items:
            sid = it.get("series_id") or (it.get("series_name"), it.get("brand_name"))
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            price = it.get("price")
            if not price and it.get("min_price"):
                price = f"{it.get('min_price')}-{it.get('max_price')}万"
            rows.append({
                "月份": yyyymm,
                "排名": it.get("rank"),
                "车型": it.get("series_name"),
                "车企": it.get("brand_name") or it.get("sub_brand_name"),
                "价格": price,
                "销量": it.get("count") or it.get("sale") or it.get("sales"),
            })
            added += 1

        print(f"{yyyymm} offset={offset} -> 新增 {added} / 返回 {len(items)}，total={total}, has_more={has_more}")
        if len(items) < page_size or has_more is False or (total and len(rows) >= total):
            break

        offset += len(items)
        time.sleep(float(os.environ.get("DCD_THROTTLE_SECONDS", "0.6")))

    return rows

def last_full_month_yyyymm(now_et: datetime | None = None) -> str:
    """Compute last full month in America/New_York timezone."""
    if now_et is None:
        now_et = datetime.now(ZoneInfo("America/New_York"))
    first_of_month = now_et.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_month - timedelta(days=1)
    return last_month_end.strftime("%Y%m")

def write_csv(rows, filename):
    rows = list(rows)
    rows.sort(key=lambda x: (x["月份"], x["排名"] if x["排名"] is not None else 9999))
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["月份","排名","车型","车企","价格","销量"])
        writer.writeheader()
        writer.writerows(rows)
    return filename, len(rows)

def send_email_smtp(subject, body_text, attachments, email_from, email_to_list,
                    smtp_host, smtp_port, smtp_user, smtp_pass, use_starttls=True):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(email_to_list)
    msg.set_content(body_text)

    for att_path in attachments:
        with open(att_path, "rb") as f:
            data = f.read()
        fname = os.path.basename(att_path)
        msg.add_attachment(data, maintype="text", subtype="csv", filename=fname)

    context = ssl.create_default_context()
    if use_starttls:
        with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(smtp_host, int(smtp_port), context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

def send_email_sendgrid(subject, body_text, attachments, email_from, email_to_list, sendgrid_api_key):
    # Build SendGrid v3 API request via requests (no SDK needed)
    files = []
    # Inline base64 attachments would be standard; to keep it simple and reliable on Actions,
    # we will attach as base64 per SendGrid API spec.
    import base64
    sg_attachments = []
    for path in attachments:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        sg_attachments.append({
            "content": b64,
            "type": "text/csv",
            "filename": os.path.basename(path),
            "disposition": "attachment"
        })

    payload = {
        "personalizations": [{"to": [{"email": e} for e in email_to_list]}],
        "from": {"email": email_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body_text}],
        "attachments": sg_attachments
    }
    headers = {
        "Authorization": f"Bearer {sendgrid_api_key}",
        "Content-Type": "application/json"
    }
    resp = requests.post("https://api.sendgrid.com/v3/mail/send", headers=headers, data=json.dumps(payload), timeout=30)
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"SendGrid API failed: {resp.status_code} {resp.text}")

def main():
    # Inputs
    provider = os.environ.get("MAIL_PROVIDER", "SMTP").upper()  # SMTP or SENDGRID
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")  # allow comma-separated
    if not email_to:
        raise SystemExit("EMAIL_TO is required (comma-separated for multiple recipients).")
    email_to_list = [e.strip() for e in email_to.split(",") if e.strip()]

    # Optional fetch config
    new_energy_env = os.environ.get("DCD_NEW_ENERGY_TYPE")
    new_energy_type = None
    if new_energy_env in ("1","2"):
        new_energy_type = int(new_energy_env)

    # Determine the month range
    force_yyyymm = os.environ.get("FORCE_YYYYMM") or os.environ.get("INPUT_FORCE_MONTH")  # workflow_dispatch support
    if force_yyyymm:
        start_yyyymm = end_yyyymm = int(force_yyyymm)
    else:
        start_env = os.environ.get("DCD_START_YYYYMM")  # e.g., 202401
        if start_env:
            start_yyyymm = int(start_env)
            end_yyyymm = int(last_full_month_yyyymm())
        else:
            # Default: fetch only last full month
            yyyymm = int(last_full_month_yyyymm())
            start_yyyymm = end_yyyymm = yyyymm

    print(f"Fetch range: {start_yyyymm} -> {end_yyyymm} (new_energy_type={new_energy_type})")

    # Fetch
    all_rows = []
    for mm in month_iter(start_yyyymm, end_yyyymm):
        page_size = int((os.environ.get("DCD_PAGE_SIZE") or "150").strip())
        month_rows = fetch_month_all(mm, page_size=page_size, new_energy_type=new_energy_type)
        all_rows.extend(month_rows)

    # Write CSV
    outname = f"dongchedi_sales_{start_yyyymm}_{end_yyyymm}"
    if new_energy_type == 1:
        outname += "_bev"
    elif new_energy_type == 2:
        outname += "_phev"
    outname += ".csv"
    csv_path, nrows = write_csv(all_rows, outname)

    # Email
    subject = f"[Dongchedi] 销量数据 {start_yyyymm}—{end_yyyymm} 共 {nrows} 行"
    body = (
        f"您好，\n\n"
        f"已抓取 {start_yyyymm}—{end_yyyymm} 的销量数据，共 {nrows} 行。\n"
        f"附件为 CSV 文件（UTF-8 带 BOM）。\n\n"
        f"参数：new_energy_type={new_energy_type or '全部'}；page_size={os.environ.get('DCD_PAGE_SIZE','150')}；"
        f"调度时区：America/New_York（逻辑按该时区计算上一个完整月份）。\n\n"
        f"如需强制抓取某月，可在手动触发工作流时填 YYYYMM。\n"
        f"— 自动发送（GitHub Actions / Cron）"
    )
    attachments = [csv_path]

    if provider == "SMTP":
        smtp_host = os.environ.get("SMTP_HOST")
        smtp_port = os.environ.get("SMTP_PORT", "587")
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASS")
        if not all([smtp_host, smtp_port, smtp_user, smtp_pass, email_from]):
            raise SystemExit("SMTP config missing: need SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM.")
        send_email_smtp(subject, body, attachments, email_from, email_to_list,
                        smtp_host, smtp_port, smtp_user, smtp_pass)
        print("Email sent via SMTP.")
    elif provider == "SENDGRID":
        api_key = os.environ.get("SENDGRID_API_KEY")
        if not (api_key and email_from):
            raise SystemExit("SENDGRID config missing: need SENDGRID_API_KEY and EMAIL_FROM.")
        send_email_sendgrid(subject, body, attachments, email_from, email_to_list, api_key)
        print("Email sent via SendGrid.")
    else:
        raise SystemExit("MAIL_PROVIDER must be SMTP or SENDGRID.")

if __name__ == "__main__":
    main()
