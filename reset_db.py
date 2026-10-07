"""
一键清空并重置演示数据
=============================
用法（在项目根目录 D:\\pythonProject\\shop_demo 下，venv 激活）：

    python reset_db.py

效果：
    清空 users / carts / orders / order_items / products / after_sales / reviews 全部数据，
    自动恢复 5 件示例商品 + admin/admin123 管理员账号，
    并把每张表的自增 id 重置回 1（商品永远从 id=1 开始）。

适合：反复测试后表太乱、毕设演示前恢复干净环境、跑 pytest 前。
注意：会删除所有业务数据（包括你注册的账号和订单），商品恢复成初始 5 件；
      app/static/images/ 里的图片文件不受影响，商品 id 重置后图片照常显示。
"""
from app import create_app, db
from app.models import User, Product, Cart, Order, OrderItem, Address, LogisticsTrack, AfterSale, Review
from app.seed import init_data

app = create_app()

with app.app_context():
    dialect = db.engine.dialect.name  # mysql / sqlite

    # MySQL 临时关闭外键检查；SQLite 不支持该语法，跳过
    if dialect == "mysql":
        db.session.execute(db.text("SET FOREIGN_KEY_CHECKS=0"))

    deleted = {
        "商品评价": Review.query.delete(),
        "售后单": AfterSale.query.delete(),
        "物流轨迹": LogisticsTrack.query.delete(),
        "订单明细": OrderItem.query.delete(),
        "订单": Order.query.delete(),
        "收货地址": Address.query.delete(),
        "购物车": Cart.query.delete(),
        "商品": Product.query.delete(),
        "用户": User.query.delete(),
    }
    db.session.commit()

    if dialect == "mysql":
        db.session.execute(db.text("SET FOREIGN_KEY_CHECKS=1"))
        # 重置自增 id：TRUNCATE 在表被外键引用时会报错（1701），
        # 用 ALTER TABLE ... AUTO_INCREMENT=1 不受外键限制
        for table in ["reviews", "after_sales", "logistics_tracks", "order_items", "orders",
                      "addresses", "carts", "products", "users"]:
            db.session.execute(db.text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))
        db.session.commit()
    # SQLite 的 rowid 在空表后自动从 1 复用，无需处理

    # 重新灌入初始数据（5 件示例商品 + admin 管理员）
    init_data()

    print("=" * 42)
    print("数据库已重置完成！")
    for name, count in deleted.items():
        print(f"  清空 {name} {count} 条")
    print(f"  恢复商品 {Product.query.count()} 件（id 从 1 开始）")
    print("  管理员账号：admin / admin123")
    print("=" * 42)
