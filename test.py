from pathlib import Path
p = Path('~/canvas-secrets.key').expanduser()
print("Looking for:", p)
print("Exists?     ", p.exists())
if p.exists():
    with p.open('r', encoding='utf-8') as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    print("Line count:", len(lines))
    if len(lines) >= 2:
        print("URL:", lines[0])
        print("Token (head):", lines[1][:12] + "…")