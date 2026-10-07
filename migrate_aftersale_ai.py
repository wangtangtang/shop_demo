"""
售后智能初审字段迁移脚本（老库升级用）
=====================================
用法（项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python migrate_aftersale_ai.py

效果：
    给 after_sales（售后单）表补 3 个智能初审字段：
      - ai_suggestion  VARCHAR(20)  规则引擎建议：approve建议同意 / reject建议拒绝 / manual需人工核实
      - ai_reason      TEXT         给管理员看的可解释理由
      - ai_analyzed_at DATETIME     初审分析时间
    三个字段均可空：存量历史售后单没有初审结果，页面上不显示建议徽标；
    新提交的售后单在创建时自动分析并写入。建议只作参考，最终状态仍以管理员
    在后台点「同意 / 拒绝」为准（人在回路）。

脚本幂等，可重复执行：字段已存在自动跳过。不删任何数据，不用 reset_db。
同时支持 MySQL（information_schema 判断列存在性）和 SQLite（PRAGMA table_info）。
说明：全新建库时三列由 db.create_all() 自动创建，应用启动时 ensure_columns()
      也会自动补列；本脚本提供给不方便重启应用、想手动迁移的老库使用。
"""
from sqlalchemy import text
from app import create_app, db

app = create_app()


def column_exists(conn, dialect, table_name, column_name):
    """列是否存在。MySQL 查 information_schema.columns；SQLite 查 PRAGMA table_info。"""
    if dialect == "mysql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_schema = DATABASE() "
                 "AND table_name = :t AND column_name = :c"),
            {"t": table_name, "c": column_name}).first()
    else:
        row = conn.execute(
            text(f"PRAGMA table_info({table_name})")).fetchall()
        # PRAGMA table_info 每行：(cid, name, type, notnull, dflt_value, pk)
        return any(r[1] == column_name for r in row)
    return row is not None


# (列名, 补列 DDL) —— 与 app/models.py 中 AfterSale 的列定义保持一致
PATCH_COLUMNS = [
    ("ai_suggestion", "ALTER TABLE after_sales ADD COLUMN ai_suggestion VARCHAR(20) NULL"),
    ("ai_reason", "ALTER TABLE after_sales ADD COLUMN ai_reason TEXT NULL"),
    ("ai_analyzed_at", "ALTER TABLE after_sales ADD COLUMN ai_analyzed_at DATETIME NULL"),
]

with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite
    print(f"检测到数据库类型：{dialect}")

    with db.engine.begin() as conn:
        for col, ddl in PATCH_COLUMNS:
            if column_exists(conn, dialect, "after_sales", col):
                print(f"after_sales.{col} 列已存在，跳过。")
            else:
                conn.execute(text(ddl))
                print(f"迁移完成：after_sales 表已添加 {col} 列。")

    print("=" * 48)
    print("售后智能初审字段迁移全部完成！")
    print("提示：AI 建议仅供管理员参考，最终审核结果仍以人工操作为准。")
    print("=" * 48)
