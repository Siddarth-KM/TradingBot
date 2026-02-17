import ast, sys
files = ["trade_executor.py", "main.py", "trading_bot.py"]
for f in files:
    try:
        ast.parse(open(f).read())
        print(f"{f}: OK")
    except SyntaxError as e:
        print(f"{f}: SYNTAX ERROR: {e}")
        sys.exit(1)
print("All files OK")
