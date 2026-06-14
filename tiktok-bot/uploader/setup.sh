#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== TiktokAutoUploader Setup ==="

# 1. Python dependencies
echo ""
echo "[1/5] Installing Python dependencies..."
pip install -r requirements.txt

# 2. Node dependencies for tiktok-signature
echo ""
echo "[2/5] Installing Node.js dependencies..."
if ! command -v node &>/dev/null; then
    echo "ERROR: Node.js is not installed. Please install Node.js >= 18:"
    echo "  https://nodejs.org/en/download"
    exit 1
fi
cd tiktok_uploader/tiktok-signature
npm install
cd "$SCRIPT_DIR"

# 3. Playwright browser binary
echo ""
echo "[3/5] Installing Playwright Chromium browser..."
npx --prefix tiktok_uploader/tiktok-signature playwright install chromium

# 4. Create required directories
echo ""
echo "[4/5] Creating required directories..."
mkdir -p CookiesDir VideosDirPath output

# 5. Create .env from example if missing
echo ""
echo "[5/5] Checking .env file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it if needed."
else
    echo ".env already exists, skipping."
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Login:   python3 cli.py login -n <username>"
echo "  2. Upload:  python3 cli.py upload --user <username> -v 'video.mp4' -t 'Title'"
echo "  3. Help:    python3 cli.py -h"
