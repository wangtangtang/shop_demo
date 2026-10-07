"""热销榜历史污染一次性清理脚本
==============================================================================
背景：
    test_admin_stats.py 早期版本的「热销榜快照」测试【没有自清理】，每跑一次
    pytest 就留下 2 个大成交量商品（统计专用A_xxx 2000 件 / 统计专用B_xxx
    1999 件）。重复跑 N 次后，这些历史商品把热销榜 limit=20 的名额占满，
    当次新建商品挤不进榜，test_top_products_snapshot_name_and_order 必挂。

    新版测试已加 try/finally 自清理。本脚本只用来【清理一次库里的历史残留】，
    清理后即可删除，幂等可重复跑。

用法（项目根目录 D:\\pythonProject\\shop_demo）：
    python scripts/cleanup_top_history.py
"""
import os
import re
import sys

# 让脚本能 import 到 app（scripts/ 在项目根下一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 匹配无自清理时代留下的商品名：统计专用A_<时间戳> / 统计专用B_<时间戳>
HISTORY_PATTERN = re.compile(r"^统计专用[AB]_\d+$")


def main():
    from app import create_app, db
    from app.models import Product

    app = create_app()
    with app.app_context():
        products = Product.query.all()
        targets = [p for p in products if HISTORY_PATTERN.match(p.name or "")]
        if not targets:
            print("没有发现热销榜历史残留商品，无需清理。")
            return
        ids = [p.id for p in targets]
        print(f"发现 {len(targets)} 个历史残留商品：")
        for p in targets:
            print(f"  - id={p.id} name={p.name}")

        # 复用测试文件里的清理逻辑（先确保 test_api 在 path 上）
        test_api_dir = os.path.join(_ROOT, "test_api")
        sys.path.insert(0, test_api_dir)
        from test_admin_stats import _db_cleanup_products
        ok = _db_cleanup_products(ids)
        if not ok:
            print("清理失败：无法执行数据库删除。")
            sys.exit(1)
        print(f"已清理 {len(targets)} 个商品及其订单，热销榜恢复干净。")


if __name__ == "__main__":
    main()
