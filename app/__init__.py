from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def ensure_columns():
    """老数据库升级补丁：缺的列自动 ALTER 补上，不用删库重来，数据保留。"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)

    # users 表补 is_admin
    try:
        user_cols = [c["name"] for c in inspector.get_columns("users")]
    except Exception:
        return  # 表还没建，create_all 会建
    if "is_admin" not in user_cols:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0"))
        print("[升级] 已自动给 users 表补上 is_admin 列")

    # products 表补 image 列（商品图片文件名，与商品 id 解耦）
    try:
        product_cols = [c["name"] for c in inspector.get_columns("products")]
    except Exception:
        product_cols = []
    if "image" not in product_cols:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE products ADD COLUMN image VARCHAR(200) DEFAULT ''"))
        print("[升级] 已自动给 products 表补上 image 列")

    # orders 表补支付相关列
    try:
        order_cols = [c["name"] for c in inspector.get_columns("orders")]
    except Exception:
        order_cols = []
    order_patches = {
        "pay_channel": "ALTER TABLE orders ADD COLUMN pay_channel VARCHAR(20) DEFAULT ''",
        "trade_no": "ALTER TABLE orders ADD COLUMN trade_no VARCHAR(64) DEFAULT ''",
        "paid_at": "ALTER TABLE orders ADD COLUMN paid_at DATETIME NULL",
        # 地址簿 + 物流功能新增列（addresses/logistics_tracks 两张新表由 create_all 自动建）
        "address_snapshot": "ALTER TABLE orders ADD COLUMN address_snapshot TEXT NULL",
        "logistics_company": "ALTER TABLE orders ADD COLUMN logistics_company VARCHAR(50) NULL",
        "tracking_no": "ALTER TABLE orders ADD COLUMN tracking_no VARCHAR(64) NULL",
        "shipped_at": "ALTER TABLE orders ADD COLUMN shipped_at DATETIME NULL",
        # 支付单关闭（顾客返回重选渠道时作废当前支付单，旧二维码回调被拒）
        "pay_closed": "ALTER TABLE orders ADD COLUMN pay_closed INTEGER DEFAULT 0",
        "pay_closed_at": "ALTER TABLE orders ADD COLUMN pay_closed_at DATETIME NULL",
    }
    for col, sql in order_patches.items():
        if col not in order_cols:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
            print(f"[升级] 已自动给 orders 表补上 {col} 列")

    # after_sales 表补售后智能初审三列（建议值/可解释理由/分析时间；老工单为 NULL）
    try:
        aftersale_cols = [c["name"] for c in inspector.get_columns("after_sales")]
    except Exception:
        aftersale_cols = []
    aftersale_patches = {
        "ai_suggestion": "ALTER TABLE after_sales ADD COLUMN ai_suggestion VARCHAR(20) NULL",
        "ai_reason": "ALTER TABLE after_sales ADD COLUMN ai_reason TEXT NULL",
        "ai_analyzed_at": "ALTER TABLE after_sales ADD COLUMN ai_analyzed_at DATETIME NULL",
    }
    for col, sql in aftersale_patches.items():
        if col not in aftersale_cols:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
            print(f"[升级] 已自动给 after_sales 表补上 {col} 列")


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")
    # Flask session（页面登录态 cookie）密钥：config 未配 SECRET_KEY 时用兜底值，
    # 保证老 config 也能跑；生产环境务必在 config.py 设置 SECRET_KEY
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = "shop_demo_demo_secret_2026"

    db.init_app(app)

    from app.routes import api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    # 注册页面路由（只有合并了前端页面的项目才有这个模块）
    try:
        from app.pages import pages_bp
        app.register_blueprint(pages_bp)
    except ImportError:
        pass

    # 创建表、补字段、初始化示例数据
    with app.app_context():
        # 新表（含售后 after_sales、地址簿 addresses、物流轨迹 logistics_tracks）
        # 由 create_all 自动建；老库补新表可跑对应 migrate_*.py 脚本
        db.create_all()
        ensure_columns()
        from app.seed import init_data
        init_data()

    return app
