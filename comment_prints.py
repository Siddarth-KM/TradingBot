import re

# Read the file
with open(r'c:\Users\sidda\Downloads\TradingBot\main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Process each line
modified_lines = []
i = 0

while i < len(lines):
    line = lines[i]
    stripped = line.lstrip()
    indent = line[:len(line) - len(stripped)]
    
    # Check if line starts with print( (accounting for whitespace)
    if stripped.startswith('print('):
        # Comment out the line
        modified_lines.append(f"{indent}# {stripped}")
        
        # Check if this print spans multiple lines (unclosed parenthesis)
        open_parens = stripped.count('(') - stripped.count(')')
        while open_parens > 0 and i + 1 < len(lines):
            i += 1
            next_line = lines[i]
            next_stripped = next_line.lstrip()
            next_indent = next_line[:len(next_line) - len(next_stripped)]
            modified_lines.append(f"{next_indent}# {next_stripped}")
            open_parens += next_stripped.count('(') - next_stripped.count(')')
    else:
        modified_lines.append(line)
    
    i += 1

# Write back
with open(r'c:\Users\sidda\Downloads\TradingBot\main.py', 'w', encoding='utf-8') as f:
    f.writelines(modified_lines)

print(f"✅ Commented out all print statements in main.py")
print(f"Total lines processed: {len(lines)}")
