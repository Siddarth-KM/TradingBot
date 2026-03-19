#!/usr/bin/env python3
"""Fix tradingbot.service: add MASSIVE_API_KEY to environment."""
import os
import sys

SERVICE_FILE = "/etc/systemd/system/tradingbot.service"

# Load key from .env file instead of hardcoding
def _read_env_key(key, filepath='.env'):
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith(f'{key}='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return os.environ.get(key, "")

API_KEY = _read_env_key("MASSIVE_API_KEY")
if not API_KEY:
    print("ERROR: MASSIVE_API_KEY not found in .env or environment")
    sys.exit(1)

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
