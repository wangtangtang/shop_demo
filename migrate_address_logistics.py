"""
收货地址簿 + 物流信息 迁移脚本（老库升级用）
=====================================
用法（项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python migrate_address_logistics.py

效果：
    1. 新建 addresses（收货地址簿）表
    2. 新建 logistics_tracks（物流轨迹）表
    3. 给 orders 表增加 4 列：address_snapshot / logistics_company /
       tracking_no / shipped_at

脚本幂等，可重复执行：表/列已存在时自动跳过。不删任何数据，不用 reset_db。
同时支持 MySQL（pymysql 驱动，连接信息从 app.config 读取）和 SQLite。
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


def column_exists(conn, dialect, table_name, column_name):
    """列是否存在。MySQL 查 information_schema.columns；SQLite 用 PRAGMA table_info。"""
    if dialect == "mysql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_schema = DATABASE() AND table_name = :t "
                 "AND column_name = :c"),
            {"t": table_name, "c": column_name}).first()
        return row is not None
    else:
        # PRAGMA 不支持参数绑定，table_name 是本脚本内部常量，无注入风险
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return any(r[1] == column_name for r in rows)


with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite
    print(f"检测到数据库类型：{dialect}")

    # 主键自增写法：MySQL 用 AUTO_INCREMENT，SQLite 用 INTEGER PRIMARY KEY 隐式 rowid
    pk = "INT PRIMARY KEY AUTO_INCREMENT" if dialect == "mysql" else "INTEGER PRIMARY KEY"

    # ---- 1. 建 addresses 表 ----
    with db.engine.begin() as conn:
        if table_exists(conn, dialect, "addresses"):
            print("addresses 表已存在，跳过建表。")
        else:
            conn.execute(text(f"""
                CREATE TABLE addresses (
                    id {pk},
                    user_id INT NOT NULL,
                    receiver_name VARCHAR(50) NOT NULL,
                    receiver_phone VARCHAR(20) NOT NULL,
                    region VARCHAR(200) NOT NULL,
                    detail TEXT NOT NULL,
                    is_default INT NOT NULL DEFAULT 0,
                    created_at DATETIME NULL
                )
            """))
            print("建表完成：addresses（收货地址簿）。")

    # ---- 2. 建 logistics_tracks 表 ----
    with db.engine.begin() as conn:
        if table_exists(conn, dialect, "logistics_tracks"):
            print("logistics_tracks 表已存在，跳过建表。")
        else:
            conn.execute(text(f"""
                CREATE TABLE logistics_tracks (
                    id {pk},
                    order_id INT NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    info TEXT,
                    track_time DATETIME NULL,
                    created_at DATETIME NULL
                )
            """))
            print("建表完成：logistics_tracks（物流轨迹）。")

    # ---- 3. orders 表补 4 列 ----
    order_patches = {
        "address_snapshot": "ALTER TABLE orders ADD COLUMN address_snapshot TEXT NULL",
        "logistics_company": "ALTER TABLE orders ADD COLUMN logistics_company VARCHAR(50) NULL",
        "tracking_no": "ALTER TABLE orders ADD COLUMN tracking_no VARCHAR(64) NULL",
        "shipped_at": "ALTER TABLE orders ADD COLUMN shipped_at DATETIME NULL",
    }
    with db.engine.begin() as conn:
        for col, sql in order_patches.items():
            if column_exists(conn, dialect, "orders", col):
                print(f"orders.{col} 列已存在，跳过。")
            else:
                conn.execute(text(sql))
                print(f"迁移完成：orders 表已添加 {col} 列。")

    print("=" * 48)
    print("收货地址簿 + 物流信息迁移全部完成！")
    print("=" * 48)
