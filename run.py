"""Start the dashboard:  python run.py            (real data)
                       python run.py --demo     (sample ads + sample customers to try things)
Then open http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import os
import sys

if sys.version_info < (3, 10):
    sys.exit("محتاج Python 3.10 أو أحدث")

from app import config  # noqa: E402

config.load_env()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="sample ads and customers, nothing real is touched")
    parser.add_argument("--no-scheduler", action="store_true", help="do not run agents on schedule")
    args = parser.parse_args()
    if args.demo:
        os.environ["SALES_TEAM_DEMO"] = "1"
        os.environ.setdefault("SALES_TEAM_DATA", str(config.BASE_DIR / "data-demo"))
        config.DATA_DIR = config.Path(os.environ["SALES_TEAM_DATA"])

    import uvicorn
    from app import db, demo
    from app.server import create_app

    db.init()
    if args.demo and demo.seed():
        print("اتضافت بيانات تجريبية.")
    note = ""
    if args.demo:
        note = "   (بيانات تجريبية: إعلانات وعملاء وهميين)"
    elif config.demo_mode():
        note = "   (Meta لسه مش متوصّل: كمّل من صفحة «الإعداد أول مرة»)"
    print(f"افتح: http://{config.HOST}:{config.port()}" + note)
    uvicorn.run(create_app(no_scheduler=args.no_scheduler), host=config.HOST, port=config.port(), log_level="warning")


if __name__ == "__main__":
    main()
