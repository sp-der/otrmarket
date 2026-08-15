from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.storage.database import get_connection

html = (root / "src/dashboard/static/index.html").read_text()
js = (root / "src/dashboard/static/app.js").read_text()
css = (root / "src/dashboard/static/styles.css").read_text()

assert "Evaluation Guard" in html
assert "styles.css?v=4.5" in html
assert "app.js?v=4.5" in html
assert "renderEvaluation" in js
assert "All-Time P/L" in html
assert "Today's P/L" in html
assert "<th>P/L</th>" in html
assert "display_result_dollars" in js
assert ".pnl-positive" in css and ".pnl-negative" in css
assert ".prop-guard-panel" in css

entry, stop, target = normalize_trade_prices("NQ", "bearish", 29877.88, 29875.99, 29869.75)
geometry = validate_trade_geometry("NQ", "bearish", entry, stop, target)
assert not geometry.valid, "Known inverted short example must be rejected"

connection = get_connection()
columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
assert {"risk_dollars", "result_dollars", "guard_reason"}.issubset(columns)
snapshot = EvaluationRiskGuard(EvaluationConfig()).snapshot(connection)
connection.close()
assert snapshot["profile"] == "LUCID_PRO_50K"
assert snapshot["mll_floor"] >= 48_000

print("Operation 4.5 verification: OK")
print(f"Prop guard: {snapshot['profile']} / {snapshot['status']}")
