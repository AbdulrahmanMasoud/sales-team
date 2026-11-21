#!/bin/bash
# Update to the latest version on GitHub without touching your data (data/) or tokens (.env).
# Everything is inside { }: bash reads the whole block before running it, so this file can safely
# replace itself during the update.
{
cd "$(dirname "$0")" || exit 1
REPO="AbdulrahmanMasoud/sales-team"
BRANCH="main"
PORT=8765

say() { printf '\n%s\n' "$1"; }
finish() { say "$1"; echo; read -r -p "دوس Enter عشان تقفل الشباك ده..." _; exit "${2:-0}"; }

LOCAL_VERSION="$(cat VERSION 2>/dev/null || echo 0)"
say "بدوّر على تحديث..."
RAW="${SALES_TEAM_UPDATE_RAW:-https://raw.githubusercontent.com/$REPO/$BRANCH}"
TARBALL="${SALES_TEAM_UPDATE_TAR:-https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH}"
REMOTE_VERSION="$(curl -fsSL --max-time 20 "$RAW/VERSION" 2>/dev/null | tr -d '[:space:]')"
if ! [[ "$REMOTE_VERSION" =~ ^[0-9][0-9.]{0,30}$ ]]; then
  finish "مش قادر أوصل لـGitHub دلوقتي. اتأكد إن النت شغال وجرّب تاني." 1
fi
if [ "$REMOTE_VERSION" = "$LOCAL_VERSION" ]; then
  finish "انت على آخر نسخة ($LOCAL_VERSION). مفيش حاجة تتحدّث."
fi
say "فيه نسخة جديدة: $REMOTE_VERSION (عندك $LOCAL_VERSION)."

# 1) the app must not be running while its files change
if lsof -ti tcp:$PORT >/dev/null 2>&1; then
  say "البرنامج شغال. هقفله الأول..."
  lsof -ti tcp:$PORT | xargs kill 2>/dev/null
  sleep 2
fi

# 2) download the new version into a temporary folder
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if ! curl -fsSL --max-time 300 "$TARBALL" -o "$TMP/new.tar.gz"; then
  finish "التنزيل فشل. مفيش أي حاجة اتغيرت عندك. جرّب تاني بعد شوية." 1
fi
mkdir "$TMP/new"
if ! tar -xzf "$TMP/new.tar.gz" -C "$TMP/new" --strip-components 1 || [ ! -f "$TMP/new/run.py" ]; then
  finish "الملف اللي نزل مش سليم. مفيش أي حاجة اتغيرت عندك." 1
fi

# 3) safety copy of your data before anything changes
if [ -d data ]; then
  STAMP="$(date +%Y-%m-%d_%H-%M)"
  mkdir -p backups
  cp -R data "backups/data_$STAMP" && say "عملت نسخة احتياطية من بياناتك في backups/data_$STAMP"
  # keep the last 5 backups only
  ls -1d backups/data_* 2>/dev/null | sort -r | tail -n +6 | while read -r old; do rm -rf "$old"; done
fi

# 4) copy the new code over the old one. Your data, tokens and installed packages are never touched.
rm -rf "$TMP/new/data" "$TMP/new/data-demo" "$TMP/new/backups" "$TMP/new/.env" "$TMP/new/.venv"
cp -R "$TMP/new/." ./ || finish "حصلت مشكلة وأنا بنسخ الملفات. بياناتك في backups. ابعت سكرين شوت للي بيساعدك." 1
# files renamed in 2026.11.21.2 (Arabic names -> English): remove the old copies
rm -f "./تشغيل.command" "./تحديث.command" "./تسجيل دخول Claude.command" "./ابدأ هنا.md"
chmod +x ./*.command 2>/dev/null
xattr -dr com.apple.quarantine . 2>/dev/null

say "اتحدّث لنسخة $REMOTE_VERSION. هشغّل البرنامج دلوقتي..."
sleep 1
exec "./Start.command"
}
exit
