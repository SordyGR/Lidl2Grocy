"""Read-only Grocy API client for Phase 1 product linking."""
import time
import hashlib
import unicodedata
import requests

TIMEOUT = 10
_CACHE = {"key": None, "at": 0.0, "catalog": None}
_LOOKUPS = {"key": None, "at": 0.0, "data": None}
CACHE_TTL = 300
STALE_TTL = 1800
CONFIG_HINT = "Set GROCY_URL (with /api, e.g. https://grocy.example.com/api) + GROCY_API_KEY"


class GrocyConfigError(Exception):
    pass


class GrocyApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Grocy API {status}: {body[:200]}")
        self.status = status
        self.body = body


def get_config():
    import settings_store as ss
    base_url = (ss.get("GROCY_URL") or "").strip().rstrip("/")
    api_key = (ss.get("GROCY_API_KEY") or "").strip()
    if not base_url or not api_key:
        raise GrocyConfigError(CONFIG_HINT)
    return base_url, api_key


def _headers(api_key):
    return {"GROCY-API-KEY": api_key, "Accept": "application/json"}


def _get(base_url, api_key, path):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=_headers(api_key), timeout=TIMEOUT)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json()
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def list_products(base_url, api_key):
    data = _get(base_url, api_key, "/objects/products")
    return data if isinstance(data, list) else []


def list_barcodes(base_url, api_key):
    try:
        data = _get(base_url, api_key, "/objects/product_barcodes")
    except GrocyApiError as e:
        if e.status == 404:
            return []
        raise
    return data if isinstance(data, list) else []


def fetch_catalog(base_url, api_key):
    h = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    key = (base_url, h)
    now = time.time()
    if _CACHE["key"] == key and (now - _CACHE["at"]) < CACHE_TTL:
        return _CACHE["catalog"]
    try:
        products = list_products(base_url, api_key)
        barcodes = list_barcodes(base_url, api_key)
    except Exception:
        cached = _CACHE.get("catalog")
        if _CACHE["key"] == key and cached is not None and (now - _CACHE["at"]) < STALE_TTL:
            stale = dict(cached)
            stale["stale"] = True
            return stale
        raise
    by_id = {int(p["id"]): p for p in products if "id" in p}
    names = [str(p.get("name", "")) for p in products]
    barcode_to_ids = {}
    for b in barcodes:
        code = _norm_barcode(b.get("barcode", ""))
        pid = b.get("product_id")
        if not code or pid is None:
            continue
        barcode_to_ids.setdefault(code, []).append(int(pid))
    catalog = {
        "products": products,
        "by_id": by_id,
        "names": names,
        "barcode_to_ids": barcode_to_ids,
        "cached_at": now,
        "stale": False,
    }
    _CACHE.update({"key": key, "at": now, "catalog": catalog})
    return catalog


def find_orphans(linked_ids, catalog):
    by_id = catalog.get("by_id", {})
    return [i for i in linked_ids if i not in by_id]


from rapidfuzz import process, fuzz


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s):
    if s is None:
        return ""
    s = _strip_accents(str(s)).upper().replace(".", "").replace(",", "")
    return " ".join(s.split())


def _norm_barcode(s):
    if s is None:
        return ""
    return "".join(ch for ch in str(s).strip() if ch not in (" ", "-", "\t"))


def suggest_grocy(clean, barcode, catalog):
    by_id = catalog.get("by_id", {})
    bmap = catalog.get("barcode_to_ids", {})
    if barcode is not None:
        code = _norm_barcode(barcode)
        if code and code in bmap:
            ids = bmap[code]
            if len(ids) == 1:
                pid = ids[0]
                return [{"grocy_id": pid, "grocy_name": str(by_id.get(pid, {}).get("name", "")), "reason": "barcode", "score": 100}]
            return [{"grocy_id": pid, "grocy_name": str(by_id.get(pid, {}).get("name", "")), "reason": "barcode-collision", "score": 100} for pid in ids]
    names = catalog.get("names", [])
    if not names or not _norm(clean):
        return []
    name_to_id = {}
    for p in catalog.get("products", []):
        n = str(p.get("name", ""))
        if n not in name_to_id:
            name_to_id[n] = int(p["id"])
    hits = process.extract(_norm(clean), [_norm(n) for n in names], scorer=fuzz.token_sort_ratio, limit=3, score_cutoff=80)
    out = []
    for norm_hit, score, idx in hits:
        if score >= 80:
            gname = names[idx]
            out.append({"grocy_id": name_to_id[gname], "grocy_name": gname, "reason": "fuzzy", "score": int(score)})
    return out


def _post(base_url, api_key, path, payload):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.post(url, headers=_headers(api_key), timeout=TIMEOUT, json=payload)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json()
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def _put(base_url, api_key, path, payload):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.put(url, headers=_headers(api_key), timeout=TIMEOUT, json=payload)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json() if r.text else None
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def add_stock(base_url, api_key, product_id, amount, price, note):
    payload = {
        "amount": amount,
        "price": price,
        "transaction_type": "purchase",
        "note": note,
    }
    data = _post(base_url, api_key, f"/stock/products/{int(product_id)}/add", payload)
    return data if isinstance(data, list) else []


def set_purchased_date(base_url, api_key, entry_id, date_iso):
    _put(base_url, api_key, f"/stock/entry/{entry_id}", {"purchased_date": date_iso})


def get_product_details(base_url, api_key, product_id):
    data = _get(base_url, api_key, f"/stock/products/{int(product_id)}")
    return data if isinstance(data, dict) else {}


def convert_to_stock_amount(qty, details):
    """Grocy's /add endpoint stores amount in stock units with no conversion,
    so translate the Lidl purchase quantity via the product's own factor."""
    try:
        factor = float(details.get("qu_conversion_factor_purchase_to_stock", 1.0) or 0) or 1.0
    except (TypeError, ValueError):
        factor = 1.0
    if factor <= 0:
        factor = 1.0
    return round(float(qty) * factor, 4), factor


def fetch_lookups(base_url, api_key):
    h = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    key = (base_url, h)
    now = time.time()
    if _LOOKUPS["key"] == key and (now - _LOOKUPS["at"]) < CACHE_TTL:
        return _LOOKUPS["data"]
    locations = _get(base_url, api_key, "/objects/locations")
    units = _get(base_url, api_key, "/objects/quantity_units")
    data = {
        "locations": locations if isinstance(locations, list) else [],
        "quantity_units": units if isinstance(units, list) else [],
        "cached_at": now,
    }
    _LOOKUPS.update({"key": key, "at": now, "data": data})
    return data


def add_barcode(base_url, api_key, product_id, barcode, qu_id=None):
    payload = {"product_id": int(product_id), "barcode": str(barcode).strip()}
    if qu_id is not None:
        payload["qu_id"] = int(qu_id)
    data = _post(base_url, api_key, "/objects/product_barcodes", payload)
    return int(data.get("created_object_id"))


def fetch_product_groups(base_url, api_key):
    groups = _get(base_url, api_key, "/objects/product_groups")
    return groups if isinstance(groups, list) else []


def create_product(base_url, api_key, name, location_id, qu_purchase, qu_stock,
                   min_stock_amount=None, qu_factor=None, product_group_id=None,
                   parent_product_id=None, description=None):
    payload = {"name": (name or "").strip()}
    if location_id is not None:
        payload["location_id"] = int(location_id)
    if qu_purchase is not None:
        payload["qu_id_purchase"] = int(qu_purchase)
    if qu_stock is not None:
        payload["qu_id_stock"] = int(qu_stock)
    if min_stock_amount is not None and str(min_stock_amount).strip() != "":
        payload["min_stock_amount"] = float(min_stock_amount)
    if qu_factor is not None and str(qu_factor).strip() != "":
        payload["qu_factor_purchase_to_stock"] = float(qu_factor)
    if product_group_id is not None:
        payload["product_group_id"] = int(product_group_id)
    if parent_product_id is not None:
        payload["parent_product_id"] = int(parent_product_id)
    if description:
        payload["description"] = description.strip()
    data = _post(base_url, api_key, "/objects/products", payload)
    return int(data.get("created_object_id"))
