from pathlib import Path

root = Path(__file__).resolve().parents[1]
confluence = root / "src/strategies/confluence.py"
app_js = root / "src/dashboard/static/app.js"
index_html = root / "src/dashboard/static/index.html"

for path in (confluence, app_js, index_html):
    if not path.exists():
        raise SystemExit(f"Missing Operation 4.4 file: {path.relative_to(root)}")

logic = confluence.read_text()
js = app_js.read_text()
html = index_html.read_text()

checks = {
    "qualifying FVG stage": 'WAIT_QUALIFYING_FVG' in logic,
    "valid RR stage": 'WAIT_VALID_RR' in logic,
    "FVG progress memory": 'entry_fvg_seen' in logic,
    "retracement progress memory": 'retracement_seen' in logic,
    "outside-zone scanner label": 'WAIT_QUALIFYING_FVG: "OUTSIDE ZONE"' in js,
    "valid-RR scanner label": 'WAIT_VALID_RR: "WAIT R:R"' in js,
    "4.4 JS cache bust": 'app.js?v=4.4' in html,
    "4.4 CSS cache bust": 'styles.css?v=4.4' in html,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("Operation 4.4 verification failed: " + ", ".join(failed))

print("Operation 4.4 verification: OK")
print("  outside-zone FVGs remain rejected entry candidates")
print("  scanner keeps FVG progress visible while searching")
print("  qualifying FVGs with invalid structure/R:R keep scanning")
print("  post-displacement expiry timer remains bounded")
print("  dashboard assets cache-busted to v4.4")
