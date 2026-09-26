"""Flask browser frontend for Lidl Parser (SQLite). Run: python app.py -> http://127.0.0.1:5000"""
import os
import sys
import sqlite3
import threading
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from db import init_db, get_db
from receipt_parser import parse_receipt, parse_receipt_date, smart_match, build_memory, extract_card_last4
import grocy as grocy_client
import settings_store as app_settings
import fetch_lidl

app = Flask(__name__)
app.secret_key = "lidl-local-dev"

_FETCH = {"running": False, "result": None, "error": None}
_FETCH_LOCK = threading.Lock()


def _run_sync():
    try:
        rc = fetch_lidl.cmd_sync(latest_only=True)
        if isinstance(rc, int) and rc != 0:
            user, pw = fetch_lidl.get_lidl_creds()
            if not user or not pw:
                _FETCH["error"] = ("No Lidl credentials saved — enter them "
                                   "in Settings, then sync again")
            else:
                _FETCH["error"] = ("Lidl auto-login failed — Lidl may be blocking "
                                   "automation; use the paste fallback on the "
                                   "dashboard")
        else:
            _FETCH["result"] = rc
    except Exception as e:
        _FETCH["error"] = str(e)[:300]
    finally:
        _FETCH["running"] = False


@app.route("/fetch/run", methods=["POST"])
def fetch_run():
    with _FETCH_LOCK:
        if _FETCH["running"]:
            return jsonify({"started": False, "reason": "already running"})
        _FETCH.update({"running": True, "result": None, "error": None})
    threading.Thread(target=_run_sync, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/fetch_status")
def api_fetch_status():
    conn = get_db()
    last = conn.execute("SELECT MAX(fetched_at) AS at FROM fetch_state").fetchone()["at"]
    conn.close()
    return jsonify({"running": _FETCH["running"], "result": _FETCH["result"],
                    "error": _FETCH["error"], "last_sync": last})


@app.route("/fetch/paste", methods=["POST"])
def fetch_paste():
    import tempfile
    text = request.form.get("text", "")
    tid = (request.form.get("tid") or "").strip() or None
    if not text.strip():
        flash("No receipt text pasted")
        return redirect(url_for("dashboard"))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        res = fetch_lidl.cmd_paste(path, tid)
    except Exception as e:
        flash(f"Paste import failed: {e}")
        return redirect(url_for("dashboard"))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    flash(f"Pasted receipt saved ({res['items']} items, {res['total']:.2f} EUR)")
    return redirect(url_for("dashboard"))


@app.context_processor
def inject_theme():
    mode = request.cookies.get("theme", "system")
    if mode not in ("light", "dark", "system"):
        mode = "system"
    return {"theme": mode}


@app.route("/theme/<mode>")
def theme_set(mode):
    if mode not in ("light", "dark", "system"):
        return redirect(request.args.get("next") or url_for("dashboard"))
    resp = redirect(request.args.get("next") or url_for("dashboard"))
    resp.set_cookie("theme", mode, max_age=31536000, samesite="Lax")
    return resp


def memory_df():
    conn = get_db()
    df = pd.read_sql("SELECT raw, clean, category FROM products", conn)
    conn.close()
    return build_memory(df)


def query(sql, args=()):
    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


@app.route("/")
def dashboard():
    kpis = query("""
        SELECT (SELECT COUNT(*) FROM receipts) AS receipts,
               (SELECT COUNT(*) FROM items) AS items,
               (SELECT COALESCE(SUM(final),0) FROM items) AS spent,
               (SELECT COUNT(*) FROM items WHERE match_type='new') AS unmapped,
               (SELECT COUNT(*) FROM products) AS products
    """)[0]
    monthly = query("""
        SELECT substr(receipt_date,1,7) AS m, ROUND(SUM(i.final),2) AS total
        FROM items i JOIN receipts r ON r.id=i.receipt_id
        WHERE r.receipt_date IS NOT NULL
        GROUP BY m ORDER BY m
    """)
    by_cat = query("""
        SELECT COALESCE(category,'Misc') AS c, ROUND(SUM(final),2) AS total
        FROM items GROUP BY c ORDER BY total DESC LIMIT 12
    """)
    recent = query("SELECT * FROM receipts ORDER BY receipt_date DESC, receipt_seq ASC, id ASC LIMIT 10")
    top = query("""
        SELECT clean, COUNT(*) AS n, ROUND(SUM(final),2) AS total
        FROM items GROUP BY clean ORDER BY total DESC LIMIT 10
    """)
    user_totals = query("""
        SELECT u.name AS n, ROUND(SUM(i.final * s.share), 2) AS total
        FROM item_splits s
        JOIN items i ON i.id = s.item_id
        JOIN users u ON u.id = s.user_id
        GROUP BY u.name ORDER BY total DESC
    """)
    return render_template("dashboard.html", k=kpis, monthly=monthly,
                           by_cat=by_cat, recent=recent, top=top,
                           user_totals=user_totals)


@app.route("/receipts")
def receipts():
    rows = query("SELECT * FROM receipts ORDER BY receipt_date DESC, receipt_seq ASC, id ASC")
    return render_template("receipts.html", rows=rows)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files or files[0].filename == "":
            flash("No files selected")
            return redirect(url_for("upload"))
        mem = memory_df()
        conn = get_db()
        cur = conn.cursor()
        imported = 0
        for f in files:
            fname = f.filename
            text = f.read().decode("utf-8", errors="replace")
            df = parse_receipt(text, fname)
            if df.empty:
                flash(f"{fname}: no items parsed")
                continue
            iso_date, seq = parse_receipt_date(fname)
            total = float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum())
            card_last4 = extract_card_last4(text)
            cur.execute(
                """INSERT OR IGNORE INTO receipts (filename, receipt_date, receipt_seq, item_count, total, card_last4)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fname, iso_date, seq, len(df), round(total, 2), card_last4))
            conn.commit()
            rid = cur.execute("SELECT id FROM receipts WHERE filename=?", (fname,)).fetchone()["id"]
            cur.execute("DELETE FROM items WHERE receipt_id=?", (rid,))
            for _, item in df.iterrows():
                clean, cat, mtype, score = smart_match(item["Product"], mem)
                tot = float(item["Qty"] * item["Unit Price"])
                cur.execute(
                    """INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                                          vat, discount, total, final, match_type, match_score)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid, item["Product"], clean, cat, float(item["Qty"]),
                     float(item["Unit Price"]), float(item["VAT"]), float(item["Discount"]),
                     round(tot, 2), round(tot - float(item["Discount"]), 2), mtype, float(score)))
            imported += 1
        conn.commit()
        conn.close()
        flash(f"Imported {imported} receipt(s)")
        return redirect(url_for("receipts"))
    return render_template("upload.html")


@app.route("/mappings")
def mappings():
    q = request.args.get("q", "").strip()
    f = request.args.get("filter", "all")
    cat = request.args.get("cat", "").strip()
    gf = request.args.get("grocy", "all")
    tf = request.args.get("track", "all")
    sort = request.args.get("sort", "raw")
    if sort not in ("raw", "clean", "category", "barcode"):
        sort = "raw"
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"
    conn = get_db()
    sql = "SELECT * FROM products"
    clauses, args = [], []
    if f == "unmapped":
        # products whose clean equals raw (never curated) OR items with match_type new
        clauses.append("(clean = raw OR clean IS NULL)")
    if q:
        clauses.append("(raw LIKE ? OR clean LIKE ? OR category LIKE ? OR barcode LIKE ?)")
        args += [f"%{q}%"] * 4
    if cat:
        clauses.append("category = ?")
        args.append(cat)
    if gf == "linked":
        clauses.append("grocy_id IS NOT NULL")
    elif gf == "unlinked":
        clauses.append("grocy_id IS NULL")
    if tf == "tracked":
        clauses.append("grocy_track != 0")
    elif tf == "skipped":
        clauses.append("grocy_track = 0")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    count = conn.execute(f"SELECT COUNT(*) FROM products" + (" WHERE " + " AND ".join(clauses) if clauses else ""), args).fetchone()[0]
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    per_page = 100
    pages = max((count + per_page - 1) // per_page, 1)
    page = min(page, pages)
    sql += f" ORDER BY {sort} {direction} LIMIT {per_page} OFFSET {(page - 1) * per_page}"
    rows = conn.execute(sql, args).fetchall()
    new_items = conn.execute(
        "SELECT DISTINCT raw FROM items WHERE match_type='new' AND raw NOT IN (SELECT raw FROM products) LIMIT 100"
    ).fetchall()
    cats = conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
    linked_ids = [r[0] for r in conn.execute("SELECT grocy_id FROM products WHERE grocy_id IS NOT NULL").fetchall()]
    conn.close()
    grocy_error = None
    lookups = {"locations": [], "quantity_units": []}
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        grocy_configured = True
        grocy_names = catalog["names"][:2000]
        grocy_products = [{"id": p["id"], "name": str(p.get("name", ""))} for p in catalog["products"][:2000]]
        if catalog.get("stale"):
            grocy_error = "Grocy unreachable — showing cached list."
        else:
            try:
                lookups = grocy_client.fetch_lookups(base_url, api_key)
            except Exception:
                pass
    except grocy_client.GrocyConfigError:
        catalog = None
        grocy_configured = False
        grocy_names = []
        grocy_products = []
    except Exception as e:
        catalog = None
        grocy_configured = True
        grocy_names = []
        grocy_products = []
        grocy_error = f"Grocy unreachable: {str(e)[:150]}"
    orphans = set(grocy_client.find_orphans(linked_ids, catalog)) if catalog is not None else set()
    enriched = []
    for r in rows:
        d = dict(r)
        d["is_orphan"] = d.get("grocy_id") in orphans
        if grocy_configured and catalog is not None and not d.get("grocy_id"):
            try:
                d["suggestions"] = grocy_client.suggest_grocy(d.get("clean"), d.get("barcode"), catalog)
            except Exception:
                d["suggestions"] = []
        else:
            d["suggestions"] = []
        enriched.append(d)
    return render_template("mappings.html", rows=enriched, new_items=new_items, q=q, filt=f, cats=cats,
                           grocy_configured=grocy_configured, grocy_names=grocy_names,
                           grocy_error=grocy_error, orphans=sorted(orphans),
                           sort=sort, direction=direction, cat=cat, gf=gf, tf=tf,
                           page=page, pages=pages, count=count, grocy_products=grocy_products,
                           locations=lookups["locations"], quantity_units=lookups["quantity_units"])


@app.route("/mappings/link", methods=["POST"])
def mapping_link():
    raw = request.form["raw"]
    try:
        gid = int(request.form["grocy_id"])
    except (KeyError, ValueError):
        flash("Missing grocy_id")
        return redirect(url_for("mappings"))
    gname = request.form.get("grocy_name", "").strip()
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        if gid not in catalog["by_id"]:
            flash(f"Grocy #{gid} not found (stale list)")
            return redirect(url_for("mappings"))
        if not gname:
            gname = str(catalog["by_id"][gid].get("name", ""))
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("mappings"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?", (gid, gname, raw))
    conn.commit()
    conn.close()
    flash(f"Linked {raw} -> Grocy #{gid}")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/unlink", methods=["POST"])
def mapping_unlink():
    raw = request.form["raw"]
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=NULL, grocy_name=NULL, grocy_synced_at=NULL WHERE raw=?", (raw,))
    conn.commit()
    conn.close()
    flash(f"Unlinked {raw}")
    return redirect(url_for("mappings"))


@app.route("/api/grocy_status")
def api_grocy_status():
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception:
        return jsonify({"configured": False, "hint": grocy_client.CONFIG_HINT})
    try:
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        conn = get_db()
        linked = [r[0] for r in conn.execute("SELECT grocy_id FROM products WHERE grocy_id IS NOT NULL").fetchall()]
        conn.close()
        orphans = grocy_client.find_orphans(linked, catalog)
        return jsonify({"configured": True, "product_count": len(catalog["products"]), "cached_at": catalog["cached_at"], "orphans": orphans, "stale": bool(catalog.get("stale"))})
    except Exception as e:
        return jsonify({"configured": True, "error": str(e)[:200]}), 502


def resolve_push_items(receipt_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (receipt_id,)).fetchall()
    prods = {p["raw"]: dict(p) for p in conn.execute("SELECT raw, grocy_id, grocy_name, grocy_track FROM products").fetchall()}
    conn.close()
    grocy_error = None
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        grocy_configured = True
        if catalog.get("stale"):
            grocy_error = "Grocy unreachable — showing cached list."
    except grocy_client.GrocyConfigError:
        return r, [], None, False, grocy_client.CONFIG_HINT
    except Exception as e:
        return r, [], None, True, f"Grocy unreachable: {str(e)[:150]}"
    out = []
    details_cache = {}
    for i in items:
        d = dict(i)
        p = prods.get(d["raw"])
        qty = float(d.get("qty") or 0)
        if not qty:
            d.update({"grocy_id": None, "grocy_name": "", "amount": 0, "price": 0.0, "status": "bad-quantity"})
        elif p is not None and not p.get("grocy_track", 1):
            d.update({"grocy_id": p.get("grocy_id"), "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": float(d.get("final") or 0) / qty,
                        "status": "excluded-flagged"})
        elif p is None or p.get("grocy_id") is None:
            d.update({"grocy_id": None, "grocy_name": "", "amount": qty,
                        "price": float(d.get("final") or 0) / qty, "status": "excluded-no-link"})
        elif int(p["grocy_id"]) not in catalog["by_id"]:
            d.update({"grocy_id": p["grocy_id"], "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": float(d.get("final") or 0) / qty,
                        "status": "orphan-link"})
        else:
            gid = int(p["grocy_id"])
            try:
                if gid not in details_cache:
                    details_cache[gid] = grocy_client.get_product_details(base_url, api_key, gid)
                details = details_cache[gid]
            except Exception:
                details = {}
            amount, factor = grocy_client.convert_to_stock_amount(qty, details)
            unit_name = ((details.get("default_quantity_unit_purchase") or {}).get("name")
                         if isinstance(details, dict) else "") or ""
            d.update({"grocy_id": gid,
                        "grocy_name": str(catalog["by_id"][gid].get("name", "")),
                        "amount": amount, "price": float(d.get("final") or 0) / amount if amount else 0.0,
                        "purchase_qty": qty, "purchase_unit": unit_name, "qu_factor": factor,
                        "status": "ready"})
        out.append(d)
    return r, out, catalog, grocy_configured, grocy_error


def _receipt_items(rid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    conn.close()
    return rows


def _push_log(rid):
    conn = get_db()
    row = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _json_wants_json():
    return "application/json" in (request.headers.get("Accept", ""))


def _split_context(conn, r, items):
    """Users list, per-item splits dict, paid-with label; first-view equal-share seeding."""
    users = [dict(u) for u in conn.execute("SELECT id, name FROM users ORDER BY name").fetchall()]
    # First-view seeding writes equal shares — but not while rendering a
    # just-failed POST (pending flashes): a rejected split must leave the row
    # unchanged, so don't auto-create rows on that render.
    if users and not session.get("_flashes"):
        share = 1.0 / len(users)
        for it in items:
            has = conn.execute("SELECT 1 FROM item_splits WHERE item_id=?", (it["id"],)).fetchone()
            if not has:
                for u in users:
                    conn.execute(
                        "INSERT OR IGNORE INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                        (it["id"], u["id"], share))
        conn.commit()
    splits = {}
    for s in conn.execute("""
            SELECT s.item_id AS item_id, s.user_id AS user_id, s.share AS share,
                   u.name AS user_name
            FROM item_splits s JOIN users u ON u.id = s.user_id
            ORDER BY s.item_id, u.name""").fetchall():
        splits.setdefault(s["item_id"], []).append(dict(s))
    last4 = r["card_last4"] if r and "card_last4" in r.keys() else None
    if not last4:
        paid_with = "Cash"
    else:
        owner = conn.execute(
            "SELECT u.name AS name FROM card_map c JOIN users u ON u.id = c.user_id WHERE c.last4=?",
            (last4,)).fetchone()
        paid_with = owner["name"] if owner else "Card \u2022\u2022" + last4
    return users, splits, paid_with


@app.route("/receipts/<int:rid>/splits", methods=["POST"])
def receipt_splits(rid):
    try:
        item_id = int(request.form.get("item_id") or "0")
    except ValueError:
        return redirect(url_for("receipt_detail", rid=rid))
    conn = get_db()
    ok = conn.execute("SELECT 1 FROM items WHERE id=? AND receipt_id=?",
                      (item_id, rid)).fetchone()
    if not ok:
        conn.close()
        return redirect(url_for("receipt_detail", rid=rid))
    if "user_id" in request.form and request.form.get("user_id"):
        try:
            uid = int(request.form["user_id"])
        except ValueError:
            uid = 0
        if uid > 0:
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)",
                         (item_id, uid))
            conn.commit()
    elif request.form.get("shares"):
        parts = []
        try:
            for chunk in request.form["shares"].split(","):
                u, s = chunk.split(":")
                parts.append((int(u), float(s)))
        except ValueError:
            parts = []
        if parts and abs(sum(s for _, s in parts) - 1.0) < 0.001 and all(s > 0 for _, s in parts):
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            for u, s in parts:
                conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                             (item_id, u, s))
            conn.commit()
        else:
            flash("Shares must be positive and sum to 1.0 — row unchanged")
    conn.close()
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/receipts/<int:rid>/grocy-preview")
def grocy_preview(rid):
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if _json_wants_json():
        return jsonify({"configured": configured, "error": err,
                        "stale": bool(catalog and catalog.get("stale")),
                        "items": resolved})
    items = _receipt_items(rid)
    conn = get_db()
    users, splits, paid_with = _split_context(conn, r, items)
    conn.close()
    return render_template("receipt_detail.html", r=r,
                           items=items, preview=resolved,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid),
                           users=users, splits=splits, paid_with=paid_with)


@app.route("/receipts/<int:rid>")
def receipt_detail(rid):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    if not r:
        conn.close()
        return "Not found", 404
    users, splits, paid_with = _split_context(conn, r, items)
    conn.close()
    try:
        _, preview, catalog, configured, err = resolve_push_items(rid)
    except Exception:
        preview, configured, err = [], False, None
    return render_template("receipt_detail.html", r=r, items=items, preview=preview,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid),
                           users=users, splits=splits, paid_with=paid_with)


@app.route("/receipts/<int:rid>/grocy-push", methods=["POST"])
def grocy_push(rid):
    import json as _json
    force = request.form.get("force") == "1"
    conn = get_db()
    existing = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    if existing and not force:
        flash("Already pushed at {} — use Force to re-push (may duplicate entries in Grocy).".format(existing["pushed_at"]))
        return redirect(url_for("receipt_detail", rid=rid))
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if not configured:
        flash(grocy_client.CONFIG_HINT)
        return redirect(url_for("receipt_detail", rid=rid))
    if err and not resolved:
        flash(f"Grocy push aborted: {err} No entries were sent and the previous push log was kept.")
        return redirect(url_for("receipt_detail", rid=rid))
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("receipt_detail", rid=rid))
    note = f"Lidl {r['filename']} {r['receipt_date'] or ''}".strip()
    pushed, skipped, errors = 0, 0, []
    for d in resolved:
        if d["status"] == "bad-quantity":
            errors.append({"raw": d["raw"], "error": "bad quantity: qty is 0, nothing sent"})
            continue
        if d["status"] != "ready":
            skipped += 1
            continue
        item_note = note
        if d.get("purchase_unit"):
            item_note = f"{note} [{d['purchase_qty']} {d['purchase_unit']}]".strip()
        try:
            entries = grocy_client.add_stock(base_url, api_key, d["grocy_id"], d["amount"], d["price"], item_note)
            stock_id = (entries or [{}])[0].get("stock_id")
            if r["receipt_date"] and stock_id:
                try:
                    grocy_client.set_purchased_date(base_url, api_key, stock_id, r["receipt_date"])
                except Exception as e:
                    errors.append({"raw": d["raw"], "error": f"backdate failed: {str(e)[:150]}"})
            pushed += 1
        except Exception as e:
            errors.append({"raw": d["raw"], "error": str(e)[:200]})
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO grocy_pushes (receipt_id, pushed_at, pushed_count, skipped_count, errors)
           VALUES (?, datetime('now'), ?, ?, ?)""",
        (rid, pushed, skipped, _json.dumps(errors)))
    conn.commit()
    conn.close()
    failed = len([e for e in errors if "backdate" not in e.get("error", "")])
    backdated = "; ".join(e["raw"] for e in errors if "backdate" in e.get("error", ""))
    msg = f"Pushed {pushed}, skipped {skipped}, failed {failed}."
    if backdated:
        msg += f" Backdate issues: {backdated}."
    flash(msg)
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/mappings/track", methods=["POST"])
def mapping_track():
    raw = request.form.get("raw", "")
    track = 1 if request.form.get("track") == "1" else 0
    if not raw:
        flash("Missing raw")
        return redirect(url_for("mappings"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_track=? WHERE raw=?", (track, raw))
    conn.commit()
    conn.close()
    flash(f"{'Tracking' if track else 'Skipping'} {raw} for Grocy push")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/create-in-grocy", methods=["POST"])
def mapping_create_in_grocy():
    raw = request.form.get("raw", "").strip()
    name = request.form.get("name", "").strip()
    barcode = request.form.get("barcode", "").strip()
    if not raw or not name:
        flash("Missing product name")
        return redirect(url_for("mappings"))

    def _opt(key):
        v = (request.form.get(key) or "").strip()
        try:
            return int(v)
        except ValueError:
            return None

    location_id = _opt("location_id")
    qu_purchase = _opt("qu_purchase")
    qu_stock = _opt("qu_stock")
    try:
        base_url, api_key = grocy_client.get_config()
        gid = grocy_client.create_product(base_url, api_key, name, location_id, qu_purchase, qu_stock)
        if barcode:
            try:
                grocy_client.add_barcode(base_url, api_key, gid, barcode)
            except Exception as e:
                flash(f"Created Grocy #{gid} but barcode failed: {e}")
        conn = get_db()
        conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?",
                     (gid, name, raw))
        conn.commit()
        conn.close()
        flash(f"Created Grocy #{gid} and linked {raw}")
    except Exception as e:
        flash(f"Grocy create failed: {e}")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/new-item")
def mapping_new_item():
    raw = request.args.get("raw", "").strip()
    conn = get_db()
    prod = conn.execute("SELECT * FROM products WHERE raw=?", (raw,)).fetchone()
    conn.close()
    prod = dict(prod) if prod else {"raw": raw, "clean": raw, "brand": "",
                                   "barcode": "", "category": "Misc"}
    grocy_error, groups = None, []
    lookups = {"locations": [], "quantity_units": []}
    try:
        base_url, api_key = grocy_client.get_config()
        grocy_configured = True
        try:
            lookups = grocy_client.fetch_lookups(base_url, api_key)
            groups = grocy_client.fetch_product_groups(base_url, api_key)
        except Exception as e:
            grocy_error = f"Grocy lookups unavailable: {str(e)[:150]}"
    except grocy_client.GrocyConfigError:
        grocy_configured = False
    return render_template("item_new.html", prod=prod,
                           grocy_configured=grocy_configured,
                           grocy_error=grocy_error,
                           locations=lookups["locations"],
                           quantity_units=lookups["quantity_units"],
                           groups=groups)


@app.route("/mappings/new-item", methods=["POST"])
def mapping_new_item_create():
    raw = request.form.get("raw", "").strip()
    name = request.form.get("name", "").strip()
    if not raw or not name:
        flash("Missing product name")
        return redirect(url_for("mapping_new_item", raw=raw))

    def _opt(key):
        v = (request.form.get(key) or "").strip()
        if v == "":
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _dec(key):
        v = (request.form.get(key) or "").strip()
        return v or None

    barcode = (request.form.get("barcode") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    try:
        base_url, api_key = grocy_client.get_config()
        gid = grocy_client.create_product(
            base_url, api_key, name, _opt("location_id"),
            _opt("qu_purchase"), _opt("qu_stock"),
            min_stock_amount=_dec("min_stock"), qu_factor=_dec("qu_factor"),
            product_group_id=_opt("product_group_id"),
            parent_product_id=_opt("parent_product_id"),
            description=description)
        if barcode:
            try:
                grocy_client.add_barcode(base_url, api_key, gid, barcode)
            except Exception as e:
                flash(f"Created Grocy #{gid} but barcode failed: {e}")
        conn = get_db()
        conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?",
                     (gid, name, raw))
        conn.commit()
        conn.close()
        flash(f"Created Grocy #{gid} and linked {raw}")
    except Exception as e:
        flash(f"Grocy create failed: {e}")
        return redirect(url_for("mapping_new_item", raw=raw))
    return redirect(url_for("mappings"))


def _resolve_category(form):
    """Dropdown value, or the typed-in name when '+ New category' was chosen."""
    cat = form.get("category", "").strip()
    if cat == "__new__":
        cat = form.get("new_category", "").strip()
    return cat or "Misc"


@app.route("/mappings/update", methods=["POST"])
def mapping_update():
    raw = request.form["raw"]
    clean = request.form.get("clean", "").strip() or raw
    category = _resolve_category(request.form)
    barcode = request.form.get("barcode", "").strip() or None
    brand = request.form.get("brand", "").strip() or None
    unit = request.form.get("unit", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    conn = get_db()
    conn.execute(
        """UPDATE products SET clean=?, category=?, barcode=?, brand=?, unit=?, notes=?,
           updated_at=datetime('now') WHERE raw=?""",
        (clean, category, barcode, brand, unit, notes, raw))
    # re-resolve existing items with this raw so dashboard updates immediately
    conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                 (clean, category, raw))
    conn.commit()
    conn.close()
    flash(f"Updated {raw}")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/bulk-update", methods=["POST"])
def mapping_bulk_update():
    try:
        count = int(request.form.get("count", "0"))
    except ValueError:
        count = 0
    conn = get_db()
    updated = 0
    for i in range(count):
        raw = (request.form.get(f"raw_{i}") or "").strip()
        if not raw:
            continue
        clean = (request.form.get(f"clean_{i}") or "").strip() or raw
        cat = (request.form.get(f"category_{i}") or "").strip()
        if cat == "__new__":
            cat = (request.form.get(f"new_category_{i}") or "").strip()
        category = cat or "Misc"
        barcode = (request.form.get(f"barcode_{i}") or "").strip() or None
        brand = (request.form.get(f"brand_{i}") or "").strip() or None
        unit = (request.form.get(f"unit_{i}") or "").strip() or None
        notes = (request.form.get(f"notes_{i}") or "").strip() or None
        cur = conn.execute(
            """UPDATE products SET clean=?, category=?, barcode=?, brand=?, unit=?, notes=?,
               updated_at=datetime('now') WHERE raw=?""",
            (clean, category, barcode, brand, unit, notes, raw))
        if cur.rowcount:
            conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                         (clean, category, raw))
            updated += 1
    conn.commit()
    conn.close()
    flash(f"Updated {updated} mapping(s)")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/add", methods=["POST"])
def mapping_add():
    raw = request.form["raw"].strip()
    clean = request.form.get("clean", "").strip() or raw
    category = _resolve_category(request.form)
    if not raw:
        return redirect(url_for("mappings"))
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO products (raw, clean, category) VALUES (?,?,?)",
                 (raw, clean, category))
    conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                 (clean, category, raw))
    conn.commit()
    conn.close()
    return redirect(url_for("mappings"))


@app.route("/mappings/delete/<path:raw>", methods=["POST"])
def mapping_delete(raw):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE raw=?", (raw,))
    conn.commit()
    conn.close()
    return redirect(url_for("mappings"))


@app.route("/prices")
def prices():
    return render_template("prices.html")


@app.route("/api/price_history")
def api_price_history():
    clean = request.args.get("clean", "")
    rows = query("""
        SELECT r.receipt_date AS d, i.unit_price AS p, i.qty AS q
        FROM items i JOIN receipts r ON r.id=i.receipt_id
        WHERE i.clean=? AND r.receipt_date IS NOT NULL
        ORDER BY d
    """, (clean,))
    legacy = query("SELECT date AS d, price AS p FROM price_history WHERE clean=? ORDER BY d", (clean,))
    return jsonify({"live": [dict(x) for x in rows], "legacy": [dict(x) for x in legacy]})


@app.route("/api/price_history_multi")
def api_price_history_multi():
    cleans = [c for c in request.args.getlist("clean") if c.strip()][:20]
    out = {}
    for clean in cleans:
        out[clean] = [dict(x) for x in query("""
            SELECT r.receipt_date AS d, i.unit_price AS p, i.qty AS q
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE i.clean=? AND r.receipt_date IS NOT NULL
            ORDER BY d
        """, (clean,))]
    return jsonify(out)


ABC_METRICS = {
    "spend": "ROUND(SUM(i.final),2)",
    "quantity": "ROUND(SUM(i.qty),3)",
    "frequency": "COUNT(*)",
}

@app.route("/api/abc_analysis")
def api_abc_analysis():
    metric = request.args.get("metric", "spend")
    if metric not in ABC_METRICS:
        metric = "spend"
    try:
        top = int(request.args.get("top", "5"))
    except ValueError:
        top = 5
    top = max(1, min(top, 50))
    rows = query(f"""
        SELECT i.clean AS clean, {ABC_METRICS[metric]} AS v
        FROM items i JOIN receipts r ON r.id=i.receipt_id
        WHERE r.receipt_date IS NOT NULL
        GROUP BY i.clean ORDER BY v DESC
    """)
    total = sum(float(r["v"] or 0) for r in rows) or 1.0
    cum, out = 0.0, []
    for r in rows:
        cum += float(r["v"] or 0)
        pct = round(100 * cum / total, 1)
        cls = "A" if pct <= 80 else ("B" if pct <= 95 else "C")
        out.append({"clean": r["clean"], "value": r["v"],
                    "cum_pct": pct, "class": cls})
    return jsonify({"metric": metric, "rows": out[:top]})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        url = (request.form.get("GROCY_URL") or "").strip()
        if url and not url.rstrip("/").endswith("/api"):
            flash("Grocy URL must end with /api - value not saved")
        else:
            app_settings.set("GROCY_URL", url)
            app_settings.set("GROCY_API_KEY", (request.form.get("GROCY_API_KEY") or "").strip())
        app_settings.set("LIDL_USER", (request.form.get("LIDL_USER") or "").strip())
        pw = request.form.get("LIDL_PASS") or ""
        if pw:
            app_settings.set("LIDL_PASS", pw)
        flash("Settings saved")
        return redirect(url_for("settings"))
    vals = {k: app_settings.get(k) for k in ("LIDL_USER", "GROCY_URL", "GROCY_API_KEY")}
    vals["LIDL_PASS_SET"] = bool(app_settings.get("LIDL_PASS"))
    conn = get_db()
    users = [dict(r) for r in conn.execute("SELECT id, name FROM users ORDER BY name").fetchall()]
    cards = [dict(r) for r in conn.execute(
        """SELECT c.last4 AS last4, c.user_id AS user_id, u.name AS user_name
           FROM card_map c JOIN users u ON u.id = c.user_id ORDER BY c.last4""").fetchall()]
    conn.close()
    return render_template("settings.html", v=vals, users=users, cards=cards)


@app.route("/settings/users", methods=["POST"])
def settings_user_add():
    name = (request.form.get("name") or "").strip()
    if name:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/cards", methods=["POST"])
def settings_card_link():
    last4 = (request.form.get("last4") or "").strip()
    try:
        uid = int(request.form.get("user_id") or "0")
    except ValueError:
        uid = 0
    if len(last4) == 4 and last4.isdigit() and uid > 0:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO card_map (last4, user_id) VALUES (?, ?)",
                     (last4, uid))
        conn.commit()
        conn.close()
    else:
        flash("Card needs exactly 4 digits and a user")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False)
