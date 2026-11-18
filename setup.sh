#!/usr/bin/env bash
# One-time setup on a new machine: ./setup.sh
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env && chmod 600 .env
echo
echo "Done. Next: 1) edit config.yaml (profile block)  2) edit .env (AI key, optional)"
echo "        3) put your resume .docx here, set resume_docx in config.yaml"
echo "        4) python main.py login naukri ; python main.py login instahyre"
echo "        5) python main.py run --minutes 60 --dry-run --cap 5"
