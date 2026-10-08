"""Standalone e-invoice pipeline (e-invoicing.gr / PEPPOL viewer HTML).

Supermarket-agnostic front end: fetch + parse produce structured invoice
dicts; the import half reuses the shared receipt-writing core in
fetch_lib, so Lidl e-invoices merge into the existing catalog/splits/
balances, and future supermarkets only need a new front end.
Stdlib-only parsing (no bs4/lxml) to keep requirements untouched.
"""
import re
from urllib.parse import urlparse

ALLOWED_HOSTS = {"e-invoicing.gr", "www.e-invoicing.gr"}

# Known issuer AFMs → market label shown in receipts.store.
MARKETS = {
    "093683423": "Lidl",
}

_CELL = re.compile(r'<td[^>]*data-title="([^"]+)"[^>]*>(.*?)</td>', re.S | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(cell: str) -> str:
    return _WS.sub(" ", _TAG.sub("", cell).replace("&nbsp;", " ")).strip()


def _num(s: str) -> float:
    s = _WS.sub("", s.replace("\xa0", "").strip())
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def fetch_invoice_html(url: str, timeout: int = 20) -> str:
    """GET an invoice page; only e-invoicing.gr hosts are allowed.

    Accepts both the QR/ViewInvoice link from the paper receipt and the
    underlying GetInvoice API link — viewer links are resolved to the API.
    """
    import requests
    try:
        parts = urlparse(url or "")
    except Exception:
        raise ValueError("only e-invoicing.gr invoice links can be imported")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ValueError("only e-invoicing.gr invoice links can be imported")
    m = re.search(r"/edocuments/ViewInvoice/-?\d+/([0-9a-f-]+)_(\w+)",
                  parts.path or "", re.IGNORECASE)
    if m:
        doc_id, hash_token = m.groups()
        url = (f"https://{host}/api/GetInvoice?contentType=PEPPOL"
               f"&intRefDocID={doc_id}&hashToken={hash_token}"
               f"&ofenm=-1&isPreview=True")
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "Lidl2Grocy/1.0"})
    if r.status_code != 200:
        raise ValueError(f"e-invoicing.gr refused the link (HTTP {r.status_code})")
    # The viewer declares UTF-8 via meta; requests guesses from headers and
    # mojibakes the Greek text otherwise.
    r.encoding = "utf-8"
    if not r.text.strip():
        raise ValueError("e-invoicing.gr returned an empty page")
    return r.text


def parse_invoice(html: str) -> dict:
    """Parse viewer HTML into a structured invoice dict."""
    afm_m = re.search(r"Α\.Φ\.Μ\s*:\s*(\d+)", html)
    afm = afm_m.group(1) if afm_m else ""
    name_m = re.search(r"BoldBlueHeader fontSize12pt\"?>([^<>]+)<", html)
    issuer_name = _clean(name_m.group(1)) if name_m else ""
    doc_m = re.search(r"Αρ\.\s*Παραστατικού\s*:\s*([^\s<>]+)", html)
    date_m = re.search(r"Ημ/νία\s+έκδοσης\s*:\s*(\d{2})-(\d{2})-(\d{4})", html)
    if not date_m:
        raise ValueError("invoice date not found")
    dd, mm, yyyy = date_m.groups()
    mark_m = re.search(r"M\.AR\.K\s*:.*?(\d{9,20})", html, re.S)
    total_m = re.search(r"ΤΕΛΙΚΗ\s+ΑΞΙΑ.*?([\d.,]+)\s*EUR", html, re.S)
    lines = []
    for row_m in re.finditer(r"<tr>(.*?)</tr>", html, re.S | re.IGNORECASE):
        cells = {k.strip(): _clean(v)
                 for k, v in _CELL.findall(row_m.group(1))}
        if "Περιγραφή" not in cells:
            continue
        try:
            vat_raw = cells.get("ΦΠΑ%", "0").replace("%", "").strip()
            lines.append({
                "code": cells.get("Κωδικός", ""),
                "name": cells["Περιγραφή"],
                "unit": cells.get("Μ.Μ.", ""),
                "qty": _num(cells.get("Ποσότητα", "1")),
                "unit_price": _num(cells.get("Τιμή μονάδας", "0")),
                "discount": _num(cells.get("Έκπτωση", "0")),
                "vat": float(vat_raw.replace(",", ".")) if vat_raw else 0.0,
                "total": _num(cells.get("Συνολική Αξία", "0")),
            })
        except ValueError:
            continue
    if not lines:
        raise ValueError("no invoice lines found")
    if afm in MARKETS:
        market = MARKETS[afm]
    elif "LIDL" in issuer_name.upper():
        market = "Lidl"
    else:
        market = issuer_name or "Unknown"
    return {
        "market": market,
        "issuer": issuer_name,
        "afm": afm,
        "doc_number": doc_m.group(1) if doc_m else "",
        "date_iso": f"{yyyy}-{mm}-{dd}",
        "lines": lines,
        "total": _num(total_m.group(1)) if total_m else round(sum(ln["total"] for ln in lines), 2),
        "mark": mark_m.group(1) if mark_m else None,
    }


def import_invoice(conn, inv: dict, filename: str, source_url=None) -> dict:
    """Write a parsed invoice through the shared receipt core."""
    import pandas as pd
    from receipt_parser import parse_receipt_date
    from fetch_lib import write_receipt_rows
    df = pd.DataFrame([{
        "Receipt": filename,
        "Product": ln["name"],
        "Qty": ln["qty"],
        "Unit Price": ln["unit_price"],
        "VAT": ln["vat"],
        "Discount": ln["discount"],
        "Total": round(ln["qty"] * ln["unit_price"], 2),
        "Final": ln["total"],
    } for ln in inv.get("lines", [])])
    if df.empty:
        raise ValueError(f"{filename}: no items parsed")
    parsed_total = round(float(df["Final"].sum()), 2)
    if abs(parsed_total - float(inv.get("total", parsed_total))) > 0.02:
        raise ValueError(f"{filename}: total mismatch parsed={parsed_total}")
    iso_date, seq = parse_receipt_date(filename)
    return write_receipt_rows(
        conn, df, filename=filename,
        iso_date=iso_date or inv.get("date_iso", ""), seq=seq,
        total=parsed_total, card_last4=None, mark=inv.get("mark"),
        store=inv.get("market", "Unknown"), t_id=None,
        source_url=source_url)
