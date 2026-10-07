import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # 数据库连接串：
    # - 默认用本地 SQLite，无需配置
    # - 用 MySQL 请设置环境变量 DATABASE_URL，例如：
    #   DATABASE_URL=mysql+pymysql://用户名:密码@localhost:3306/shop_demo?charset=utf8mb4
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'shop.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 密钥：生产环境请设置环境变量 SECRET_KEY 为随机字符串
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-random-secret")
