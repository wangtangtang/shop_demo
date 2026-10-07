from app import db
from app.models import Product, User

# 初始 5 件商品的默认图片文件名（图片文件放在 app/static/images/ 目录）
DEFAULT_IMAGES = ["product_1.png", "product_2.png", "product_3.png",
                  "product_4.png", "product_5.png"]


def init_data():
    """首次启动时灌入 5 件示例商品 + 1 个管理员账号"""
    if Product.query.count() == 0:
        products = [
            Product(name="机械键盘", price=299.0, stock=50,
                    description="青轴机械键盘，打字手感好", image=DEFAULT_IMAGES[0]),
            Product(name="无线鼠标", price=89.0, stock=100,
                    description="静音无线鼠标，续航3个月", image=DEFAULT_IMAGES[1]),
            Product(name="4K显示器", price=1599.0, stock=20,
                    description="27寸4K IPS显示器", image=DEFAULT_IMAGES[2]),
            Product(name="USB-C 扩展坞", price=199.0, stock=80,
                    description="7合1扩展坞，支持PD充电", image=DEFAULT_IMAGES[3]),
            Product(name="降噪耳机", price=699.0, stock=30,
                    description="主动降噪，续航30小时", image=DEFAULT_IMAGES[4]),
        ]
        db.session.add_all(products)

    # 老库升级兜底：image 列为空的商品统一用占位图
    # （后台手动新增、没传图片的商品也是这张图，与商品类型无关）
    for p in Product.query.all():
        if not p.image:
            p.image = "default.svg"
        if p.is_deleted is None:  # 老库没有软删除概念，全部视为在售
            p.is_deleted = 0

    # 后台管理员账号（已存在则跳过）
    # 用法：后台管理页用 admin / admin123 登录（库内存哈希，非明文）
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", is_admin=1)
        admin.set_password("admin123")
        db.session.add(admin)

    db.session.commit()
