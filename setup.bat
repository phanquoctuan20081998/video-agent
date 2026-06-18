@echo off
REM Setup script for Video Agent on Windows

echo.
echo 🎬 Video Agent Setup Script
echo ==============================
echo.

REM Check Python version
python --version
echo ✓ Python found

REM Create virtual environment if not exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat
echo ✓ Virtual environment activated

REM Install dependencies
echo Installing dependencies...
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
echo ✓ Dependencies installed

REM Copy configuration files
if not exist ".env" (
    copy .env.example .env
    echo ✓ Created .env (please update with your API keys)
)

if not exist "config\config.toml" (
    copy config\config.example.toml config\config.toml
    echo ✓ Created config/config.toml
)

REM Create necessary directories
if not exist "temp" mkdir temp
if not exist "outputs" mkdir outputs
if not exist "logs" mkdir logs

echo.
echo ==============================
echo ✓ Setup completed successfully!
echo.
echo Next steps:
echo 1. Update .env with your API keys
echo 2. Update config/config.toml if needed
echo 3. Run: python -m src.cli test-api (to test connections)
echo 4. Run: python -m src.cli generate --topic "Your Topic"
echo.
pause
