@echo off
cd /d C:\Users\sidda\Downloads\TradingBot
set "CLAUDE_CODE_GIT_BASH_PATH=C:\Users\sidda\Git\bin\bash.exe"
set "PATH=C:\Users\sidda\Git\bin;%PATH%"
set CLAUDE_BIN=
for /f "delims=" %%i in ('dir /b /ad /o-n "C:\Users\sidda\.vscode\extensions\anthropic.claude-code-*" 2^>nul') do if not defined CLAUDE_BIN set "CLAUDE_BIN=C:\Users\sidda\.vscode\extensions\%%i\resources\native-binary\claude.exe"
if not defined CLAUDE_BIN (
    echo [%date% %time%] ERROR: claude.exe not found under .vscode\extensions\anthropic.claude-code-* >> logs\monitor.log
    exit /b 2
)
"%CLAUDE_BIN%" -p "Read agents/monitor_and_email.md and execute it" --allowedTools "Bash,Read,Write,Edit" >> logs\monitor.log 2>&1
