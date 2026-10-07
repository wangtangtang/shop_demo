"""
支付单关闭 + 商品评价迁移脚本（老库升级用）
=============================================
用法（项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python migrate_pay_close.py

效果：
    一、orders 表新增两个支付单关闭字段：
      - pay_closed     INTEGER DEFAULT 0   支付单是否被顾客主动关闭（0有效/1已关闭）
      - pay_closed_at  DATETIME NULL       主动关闭时间（关闭留痕，可追溯）
      顾客在收银台「返回重选支付方式」时旧支付单作废：旧二维码/迟到回调一律拒绝，
      防止关闭旧微信单后旧回调把订单置成已付款；pay_channel/trade_no 历史保留不删。

    二、reviews 评价新表（订单级评价：一笔订单一条评价，关联整单商品快照）：
      id / order_id(UNIQUE,FK) / user_id(FK) / rating / content /
      is_deleted / created_at
      db.create_all() 自动创建缺失的新表，已存在的表不受影响。

脚本幂等，可重复执行：列/表已存在自动跳过。不删任何数据，不用 reset_db。
同时支持 MySQL（information_schema 判断列存在性）和 SQLite（PRAGMA table_info）。
说明：全新建库时列由 db.create_all() 自动创建，应用启动时 ensure_columns()
      也会自动补列；本脚本提供给不方便重启应用、想手动迁移的老库使用。
"""
from sqlalchemy import text
from app import create_app, db
from app.models import Review  # noqa: F401  确保 reviews 表随 create_all 注册

app = create_app()


def column_exists(conn, dialect, table_name, column_name):
    """列是否存在。MySQL 查 information_schema.columns；SQLite 查 PRAGMA table_info。"""
    if dialect == "mysql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_schema = DATABASE() "
                 "AND table_name = :t AND column_name = :c"),
            {"t": table_name, "c": column_name}).first()
        return row is not None
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    # PRAGMA table_info 每行：(cid, name, type, notnull, dflt_value, pk)
    return any(r[1] == column_name for r in rows)


def table_exists(conn, dialect, table_name):
    """表是否存在。"""
    if dialect == "mysql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.tables "
                 "WHERE table_schema = DATABASE() AND table_name = :t"),
            {"t": table_name}).first()
        return row is not None
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name}).first()
    return row is not None


# (列名, MySQL 补列 DDL, SQLite 补列 DDL) —— 与 app/models.py 中 Order 定义保持一致
PATCH_COLUMNS = [
    ("pay_closed",
     "ALTER TABLE orders ADD COLUMN pay_closed INTEGER DEFAULT 0",
     "ALTER TABLE orders ADD COLUMN pay_closed INTEGER DEFAULT 0"),
    ("pay_closed_at",
     "ALTER TABLE orders ADD COLUMN pay_closed_at DATETIME NULL",
     "ALTER TABLE orders ADD COLUMN pay_closed_at DATETIME NULL"),
]

with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite
    print(f"检测到数据库类型：{dialect}")

    # 1) reviews 新表：create_all 只建不存在的表，对已有表无影响
    db.create_all()
    with db.engine.begin() as conn:
        if table_exists(conn, dialect, "reviews"):
            print("reviews 表已存在/已由 create_all 创建，跳过。")

    # 2) orders 表补支付单关闭两列
    with db.engine.begin() as conn:
        for col, ddl_mysql, ddl_sqlite in PATCH_COLUMNS:
            if column_exists(conn, dialect, "orders", col):
                print(f"orders.{col} 列已存在，跳过。")
            else:
                conn.execute(text(ddl_mysql if dialect == "mysql" else ddl_sqlite))
                print(f"迁移完成：orders 表已添加 {col} 列。")

    print("=" * 48)
    print("支付单关闭 + 商品评价迁移全部完成！")
    print("提示：旧支付单关闭后迟到回调会被拒绝，重新发起支付生成新流水号。")
    print("=" * 48)
