"""
售后申请/审核 迁移脚本（老库升级用）
=====================================
用法（项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python migrate_aftersale.py

效果：
    新建 after_sales（售后单）表：顾客对已完成订单申请售后，管理员后台审核。
    字段：id / order_id / user_id / type(退货退款/换货/价保) / reason(申请原因) /
          status(审核中/已同意/已拒绝) / admin_note(管理员备注) /
          created_at(申请时间) / handled_at(审核时间)

脚本幂等，可重复执行：表已存在时自动跳过。不删任何数据，不用 reset_db。
同时支持 MySQL（pymysql 驱动，连接信息从 app.config 读取）和 SQLite。
说明：全新建库时 after_sales 表由 db.create_all() 自动创建，本脚本仅给老库补建用。
"""
from sqlalchemy import text
from app import create_app, db

app = create_app()


def table_exists(conn, dialect, table_name):
    """表是否存在。MySQL 查 information_schema.tables；SQLite 查 sqlite_master。"""
    if dialect == "mysql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.tables "
                 "WHERE table_schema = DATABASE() AND table_name = :t"),
            {"t": table_name}).first()
    else:
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table_name}).first()
    return row is not None


with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite
    print(f"检测到数据库类型：{dialect}")

    # 主键自增写法：MySQL 用 AUTO_INCREMENT，SQLite 用 INTEGER PRIMARY KEY 隐式 rowid
    pk = "INT PRIMARY KEY AUTO_INCREMENT" if dialect == "mysql" else "INTEGER PRIMARY KEY"

    # ---- 建 after_sales 售后单表 ----
    with db.engine.begin() as conn:
        if table_exists(conn, dialect, "after_sales"):
            print("after_sales 表已存在，跳过建表。")
        else:
            conn.execute(text(f"""
                CREATE TABLE after_sales (
                    id {pk},
                    order_id INT NOT NULL,
                    user_id INT NOT NULL,
                    type VARCHAR(20) NOT NULL,
                    reason TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    admin_note VARCHAR(255) NULL,
                    created_at DATETIME NULL,
                    handled_at DATETIME NULL
                )
            """))
            print("建表完成：after_sales（售后单）。")

    print("=" * 48)
    print("售后申请/审核迁移全部完成！")
    print("=" * 48)
