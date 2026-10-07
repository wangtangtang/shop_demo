"""
软删除字段迁移脚本（老库升级用）
=====================================
用法（项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python migrate_soft_delete.py

效果：
    给 products 表增加 is_deleted 字段（0=在售，1=已下架）。
    已有数据全部置为 0（在售）。脚本可重复执行，字段已存在时自动跳过。
    不删任何数据，不用 reset_db。
"""
from sqlalchemy import text
from app import create_app, db

app = create_app()

with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite
    # MySQL: SHOW COLUMNS 第 0 列是字段名；SQLite: PRAGMA table_info 第 1 列是字段名
    if dialect == "mysql":
        cols = [c[0] for c in db.session.execute(text("SHOW COLUMNS FROM products"))]
    else:
        cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(products)"))]

    if "is_deleted" in cols:
        print("is_deleted 字段已存在，无需迁移。")
    else:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN is_deleted INT NOT NULL DEFAULT 0"))
        db.session.commit()
        print("迁移完成：products 表已添加 is_deleted 字段，存量商品全部为在售状态。")
