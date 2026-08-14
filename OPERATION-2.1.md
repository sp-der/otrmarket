# OTR Market Operation 2.1

Dashboard branding patch.

Changes:
- Black/white monochrome dashboard theme
- OTR logo in sidebar
- Removes teal/blue/red/yellow dashboard accent palette
- Makes QQQ and SPY explicitly display as index proxies
- Keeps all existing Operation 2 dashboard/API behavior

Install from repository root:

```bash
unzip -o otrmarket-operation-2.1-branding.zip -d .
python -m unittest discover -s tests -v
bash run_all.sh
```
