#!/usr/bin/env python3
"""Fix tradingbot.service: add MASSIVE_API_KEY to environment."""
import sys

SERVICE_FILE = "/etc/systemd/system/tradingbot.service"
API_KEY = "tn4NSpJrJycbY85Tn2rUcuEo_JER8jqR"

with open(SERVICE_FILE, 'r') as f:
    content = f.read()

if 'MASSIVE_API_KEY' in content:
    print("MASSIVE_API_KEY already present in service file")
    sys.exit(0)

# Add the API key line after the HOME environment line
old_line = 'Environment="HOME=/home/ubuntu"'
new_lines = old_line + '\n' + f'Environment="MASSIVE_API_KEY={API_KEY}"'

if old_line not in content:
    print(f"ERROR: Could not find '{old_line}' in service file")
    sys.exit(1)

content = content.replace(old_line, new_lines)

with open(SERVICE_FILE, 'w') as f:
    f.write(content)

print(f"Added MASSIVE_API_KEY to {SERVICE_FILE}")
