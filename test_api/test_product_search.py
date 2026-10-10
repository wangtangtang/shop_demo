"""商品搜索 / 详情 边界测试（特性分支 feature/product-search-test）

覆盖：
- 按关键字搜索命中
- 搜索无结果
- 查询存在的商品详情
- 查询不存在的商品详情（404）

自带临时 SQLite，不依赖外部服务。
"""
import os
import tempfile
import pytest
from flask import Flask

from app import db


@pytest.fixture()
def client():
    fd, db_file = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="product-search-secret",
        )
        db.init_app(app)
        from app.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")
        with app.app_context():
            db.create_all()
            from app.models import Product
            db.session.add_all([
                Product(name="机械键盘", price=299.0, stock=20),
                Product(name="无线鼠标", price=99.0, stock=30),
            ])
            db.session.commit()
            yield app.test_client()
            db.session.remove()
            db.engine.dispose()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def test_search_keyword_hits(client):
    """搜索存在的关键字「键盘」：能搜到商品"""
    r = client.get("/api/products?keyword=键盘")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data["items"]) >= 1


def test_search_no_result(client):
    """搜索不存在的关键字：返回空列表、total 为 0"""
    r = client.get("/api/products?keyword=不存在的商品xyz")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["total"] == 0
    assert data["items"] == []


def test_get_existing_product_detail(client):
    """查询存在的商品详情：返回名称、价格正确"""
    r = client.get("/api/products/1")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["name"] == "机械键盘"
    assert data["price"] == pytest.approx(299.0)


def test_get_missing_product_returns_404(client):
    """查询不存在的商品 id：必须 404"""
    r = client.get("/api/products/9999")
    assert r.status_code == 404
