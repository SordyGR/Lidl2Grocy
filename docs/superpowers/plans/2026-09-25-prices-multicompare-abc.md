# Prices Multi-Compare + ABC Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users compare multiple products' price trends on one chart with an ABC Pareto panel and stat table.

**Architecture:** Two parameterized JSON endpoints in `app.py` over existing `items`/`receipts` tables (no migrations); rewrite `prices.html` with metric switcher, top-N + search-chip selection persisted in URL params, overlaid Chart.js trends, Pareto bars with cumulative line, and a stat table.

**Tech Stack:** Flask, SQLite, Chart.js 4 (already loaded in `base.html`), vanilla JS fetch.

**Spec:** `C:\Users\sordy\Documents\OpenCodeProject1\docs\superpowers\specs\2026-09-25-prices-abc-item-creation-design.md` (Section 2)

## Global Constraints

- App lives in `C:\Users\sordy\Documents\OpenCodeProject1\Lidl Parser\` — all paths below are relative to it.
- No DB migrations; ABC/trend data comes only from `items` JOIN `receipts` (legacy `price_history` excluded, matching dashboard KPIs).
- Directory is not a git repo — no commit steps; each task ends with a verification run.
- No test harness in repo — verification uses Flask `test_client` scripts and `py_compile`, not pytest.
- Keep the existing single-product view working via default params (no `clean` selected → top-5 spend).

## Review Focus

- Product `clean` names containing quotes or `&` must not break the chart JS or the `clean=` query params — expect correct rendering and `encodeURIComponent` on every param.
- `top=0`, `top=abc`, or negative `top` must fall back to the default 5, not 500 rows — expect clamping.
- An unknown `metric=` value must fall back to `spend`, not a 500 — expect the spend ranking.
- Products with a single purchase still rank and chart (single-point dataset) — expect no crash and one dot.
- More datasets than palette colors must cycle colors, not render undefined strokes — expect `i % palette.length`.

---

### Task 1: `GET /api/price_history_multi` endpoint

**Files:**
- Modify: `app.py` (add after `api_price_history`, ~line 616)
- Verify: `test_client` JSON checks

**Interfaces:**
- Consumes: existing `query()` helper in `app.py`.
- Produces: `{clean: [{d, p, q}, ...]}` JSON consumed by Task 3's chart.

- [ ] **Step 1: Add the endpoint**

```python
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
```

Cap of 20 products bounds chart cost. Same SELECT shape as `api_price_history`
so the frontend reuses its mapping.

- [ ] **Step 2: Verify with `test_client`**

Run: `python -c "import app as a; c=a.app.test_client(); r=c.get('/api/price_history_multi?clean=A&clean=B'); print(r.status_code, type(r.get_json()).__name__)"`
Expected: `200 dict` (empty lists if those cleans don't exist — still `dict`).
Run: `python -c "import app as a; c=a.app.test_client(); r=c.get('/api/price_history_multi'); print(r.get_json())"`
Expected: `{}`.

### Task 2: `GET /api/abc_analysis` endpoint

**Files:**
- Modify: `app.py` (add after Task 1's endpoint)
- Verify: `test_client` class-math checks

**Interfaces:**
- Consumes: `query()` helper; metric parameter validated against a fixed map.
- Produces: `[{clean, value, cum_pct, class}]` JSON consumed by Task 3's Pareto panel and table.

- [ ] **Step 1: Add the endpoint with one parameterized query**

```python
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
```

`metric` is never interpolated from raw input (map lookup first) — no SQL
injection surface. Cutoffs A ≤80 / B ≤95 / C rest per spec.

- [ ] **Step 2: Verify ranking math**

Run: `python -c "import app as a; c=a.app.test_client(); j=c.get('/api/abc_analysis?metric=spend&top=5').get_json(); print(j['metric'], [(x['clean'],x['class']) for x in j['rows']])"`
Expected: `200`, metric `spend`, classes non-increasing A→B→C in row order.
Run: `python -c "import app as a; c=a.app.test_client(); print(c.get('/api/abc_analysis?metric=bogus').get_json()['metric'], c.get('/api/abc_analysis?metric=spend&top=abc').status_code)"`
Expected: `spend 200` (fallback + clamp paths).

### Task 3: Rewrite `templates/prices.html`

**Files:**
- Modify: `templates/prices.html` (full rewrite; keep `{% extends "base.html" %}`)
- Modify: `app.py` `prices()` route (~line 597) to pass through query params (no `selected` single default — template reads params)
- Verify: render check + browser check of all three metrics

**Interfaces:**
- Consumes: `/api/price_history_multi` and `/api/abc_analysis` from Tasks 1–2.
- Produces: finished page; nothing downstream.

- [ ] **Step 1: Update the `prices()` route to stop forcing a single selection**

```python
@app.route("/prices")
def prices():
    return render_template("prices.html")
```

The page bootstraps entirely from the two JSON endpoints, so no server-side
product list is needed. Delete the old `products`/`selected` query.

- [ ] **Step 2: Rewrite `prices.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>Price comparison & ABC</h1>
<form id="ctl" method="get">
<select name="metric" id="metric">
<option value="spend">Spend</option>
<option value="quantity">Quantity</option>
<option value="frequency">Frequency</option>
</select>
<label>Top <input name="top" id="top" size="3" value="5"></label>
<input id="add" placeholder="add product…" size="24" list="all-products">
<datalist id="all-products"></datalist>
<button type="submit">Apply</button>
<div id="chips"></div>
</form>
<canvas id="trend"></canvas>
<canvas id="pareto"></canvas>
<table id="stats"><tr><th>Product</th><th>Buys</th><th>Qty</th><th>Spent</th>
<th>Min</th><th>Max</th><th>Last</th><th>ABC</th></tr></table>
<script>
const PALETTE = ["#4e79a7","#f28e2b","#e15759","#76b7b2","#59a14f",
  "#edc948","#b07aa1","#ff9da7","#9c755f","#bab0ac"];
const params = new URLSearchParams(location.search);
const metric = params.get("metric") || "spend";
const top = params.get("top") || "5";
const adds = params.getAll("add");
document.getElementById("metric").value = metric;
document.getElementById("top").value = top;
async function boot(){
  const abc = await (await fetch("/api/abc_analysis?metric="
    + encodeURIComponent(metric) + "&top=" + encodeURIComponent(top))).json();
  let cleans = abc.rows.map(r=>r.clean).concat(adds);
  cleans = [...new Set(cleans)];
  const hist = await (await fetch("/api/price_history_multi?"
    + cleans.map(c=>"clean="+encodeURIComponent(c)).join("&"))).json();
  const trend = new Chart(document.getElementById("trend"), {type:"line",
    data:{datasets: cleans.map((c,i)=>({label:c,
      data:(hist[c]||[]).map(x=>({x:x.d, y:x.p})), spanGaps:true,
      borderColor: PALETTE[i % PALETTE.length]}))},
    options:{scales:{x:{type:"category"}}}});
  const vals = abc.rows.map(r=>r.value), cum = abc.rows.map(r=>r.cum_pct);
  const colors = abc.rows.map(r=>r.class==="A"?"#59a14f":r.class==="B"?"#edc948":"#e15759");
  new Chart(document.getElementById("pareto"), {type:"bar",
    data:{labels: abc.rows.map(r=>r.clean),
      datasets:[{label:metric, data:vals, backgroundColor:colors},
        {label:"cum %", data:cum, type:"line", yAxisID:"y1"}]},
    options:{scales:{y1:{position:"right", min:0, max:100}}}});
  const tb = document.getElementById("stats");
  cleans.forEach(c=>{
    const pts = hist[c]||[], ps = pts.map(x=>x.p);
    const tr = document.createElement("tr");
    const cls = (abc.rows.find(r=>r.clean===c)||{}).class || "–";
    tr.innerHTML = "<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>";
    const tds = tr.children;
    tds[0].textContent = c; tds[1].textContent = pts.length;
    tds[2].textContent = pts.reduce((s,x)=>s+x.q,0).toFixed(2);
    tds[3].textContent = pts.reduce((s,x)=>s+x.p*x.q,0).toFixed(2);
    tds[4].textContent = ps.length?Math.min(...ps).toFixed(2):"–";
    tds[5].textContent = ps.length?Math.max(...ps).toFixed(2):"–";
    tds[6].textContent = ps.length?ps[ps.length-1].toFixed(2):"–";
    tds[7].textContent = cls;
    tb.appendChild(tr);
  });
  const chips = document.getElementById("chips");
  cleans.forEach(c=>{
    const s = document.createElement("span"); s.textContent = c + " ×";
    s.style.cursor = "pointer";
    s.onclick = ()=>{
      const p = new URLSearchParams(location.search);
      const kept = p.getAll("add").filter(x=>x!==c);
      p.delete("add"); kept.forEach(x=>p.append("add",x));
      location.search = p.toString();
    };
    chips.appendChild(s);
  });
  const add = document.getElementById("add");
  add.onchange = ()=>{
    if(!add.value) return;
    const p = new URLSearchParams(location.search);
    p.append("add", add.value); location.search = p.toString();
  };
}
boot();
</script>
{% endblock %}
```

Every `clean` goes through `encodeURIComponent` (Review Focus line 1);
`textContent` (not innerHTML) for product names; palette cycles
(Review Focus line 5); single-purchase products render one dot (line 4).

- [ ] **Step 3: Verify render + metrics in browser**

Run: `python -m py_compile app.py`
Expected: silent.
Run: `python -c "import app as a; c=a.app.test_client(); print(c.get('/prices').status_code)"`
Expected: `200`.
Then open `http://127.0.0.1:5000/prices` — default top-5 spend overlay +
Pareto render; switch metric to quantity and frequency; add a pick via the
search box; remove via chip; reload with `?metric=bogus&top=abc` and confirm
spend top-5 fallback.
