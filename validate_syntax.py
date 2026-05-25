import ast, sys
files = ["main.py", "trading_bot.py", "validate_deploy.py"]
for f in files:
    try:
        ast.parse(open(f, encoding="utf-8").read())
        print(f"{f}: OK")
    except SyntaxError as e:
        print(f"{f}: SYNTAX ERROR: {e}")
        sys.exit(1)
print("All files OK")
