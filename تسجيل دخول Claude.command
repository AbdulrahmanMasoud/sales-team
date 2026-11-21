#!/bin/bash
# تسجيل دخول Claude على الجهاز ده (مرة واحدة). الفريق بيشتغل باشتراكك في Claude.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "افتح «تشغيل.command» مرة واحدة الأول، وبعدين ارجع للملف ده."
  read -n 1 -s -r -p "دوس أي زرار عشان تقفل…"; exit 1
fi
CLI="$(.venv/bin/python -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))' 2>/dev/null)"
[ -x "$CLI" ] || CLI="$(command -v claude)"
if [ -z "$CLI" ]; then
  echo "مش لاقي Claude. افتح «تشغيل.command» الأول وبعدين جرّب تاني."
  read -n 1 -s -r -p "دوس أي زرار عشان تقفل…"; exit 1
fi
echo "هيفتح Claude. لو طلب تسجيل دخول: اختار حساب الاشتراك (Claude Pro أو Max) وكمّل في المتصفح."
echo "لما يخلص ويقولك إنه جاهز، اكتب /exit ودوس Enter، واقفل النافذة، وارجع لصفحة الإعداد ودوس «جرّب Claude»."
echo
"$CLI"
