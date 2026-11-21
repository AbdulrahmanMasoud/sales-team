#!/bin/bash
# Sales team: double-click this file to start the app and open it in the browser.
cd "$(dirname "$0")" || exit 1
URL="http://127.0.0.1:8765/"
pause_and_exit() { echo; read -n 1 -s -r -p "دوس أي زرار عشان تقفل…"; exit "${1:-1}"; }

# Already running? Just open the page.
if curl -s -o /dev/null "$URL" 2>/dev/null; then
  echo "البرنامج شغال أصلاً. بفتحلك الصفحة."
  open "$URL"; exit 0
fi

# Python 3.10 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3.10 /usr/local/bin/python3 /opt/homebrew/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "محتاج تثبّت Python الأول (مرة واحدة بس)."
  echo "هفتحلك صفحة التحميل: نزّل «macOS 64-bit universal2 installer» وثبّته عادي،"
  echo "وبعدين افتح «Start.command» تاني."
  open "https://www.python.org/downloads/macos/"
  pause_and_exit 1
fi

# Virtual env and packages (first run, or when requirements.txt changes)
if [ ! -x .venv/bin/python ]; then
  echo "أول مرة: بجهّز البرنامج (دقيقتين تلاتة)…"
  "$PY" -m venv .venv || { echo "حصلت مشكلة في التجهيز."; pause_and_exit 1; }
fi
REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.req-hash 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "بنزّل المكونات…"
  .venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
  if ! .venv/bin/python -m pip install -q -r requirements.txt; then
    echo "مقدرتش أنزّل المكونات. اتأكد إن النت شغال وجرّب تاني."
    pause_and_exit 1
  fi
  echo "$REQ_HASH" > .venv/.req-hash
fi

# Open the page as soon as the server answers
( for i in $(seq 1 40); do sleep 1; if curl -s -o /dev/null "$URL"; then open "$URL"; break; fi; done ) &

echo
echo "البرنامج شغال. سيب النافذة دي مفتوحة: لو قفلتها الفريق هيقف."
echo "(الماك مش هينام طول ما النافذة مفتوحة، إلا لو قفلت غطا اللابتوب.)"
echo
exec caffeinate -i .venv/bin/python run.py
