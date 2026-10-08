"""注册 / 登录 入参校验测试（新同事 B 首次提交）

新人接手项目，先从最基础的"入口接口"练手：
验证注册接口对各类非法入参的拦截，以及正常注册后能登录。
用例只依赖临时 SQLite，不依赖外部数据库和启动服务，clone 即可跑。

和冒烟测试一样，这里手动构建只含 API 的精简 app，
db 只注册一次，避免新版 flask_sqlalchemy 的重复注册报错。
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
            SECRET_KEY="reg-validation-secret",
        )
        db.init_app(app)
        from app.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")
        with app.app_context():
            db.create_all()
            yield app.test_client()
            db.session.remove()
            db.engine.dispose()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def test_register_empty_username_or_password_rejected(client):
    """用户名或密码为空：必须 400（必填校验）"""
    r = client.post("/api/register", json={"username": "", "password": "123456"})
    assert r.status_code == 400
    assert r.get_json()["code"] == 400

    r2 = client.post("/api/register", json={"username": "abc", "password": ""})
    assert r2.status_code == 400


def test_register_username_too_short_rejected(client):
    """用户名不足 3 位：400（边界值：2 位应被拦）"""
    r = client.post("/api/register", json={"username": "ab", "password": "123456"})
    assert r.status_code == 400


def test_register_password_too_short_rejected(client):
    """密码不足 6 位：400（边界值：5 位应被拦）"""
    r = client.post("/api/register", json={"username": "okuser", "password": "12345"})
    assert r.status_code == 400


def test_register_duplicate_username_rejected(client):
    """同一用户名重复注册：400 且提示用户名已存在"""
    first = client.post("/api/register",
                        json={"username": "dupuser", "password": "123456"})
    assert first.status_code == 200

    again = client.post("/api/register",
                        json={"username": "dupuser", "password": "654321"})
    assert again.status_code == 400
    assert "已存在" in again.get_json()["msg"]


def test_register_success_then_can_login(client):
    """正常注册 → 用该账号能登录并拿到 token（闭环正向用例）"""
    reg = client.post("/api/register",
                      json={"username": "gooduser", "password": "123456"})
    assert reg.status_code == 200

    login = client.post("/api/login",
                        json={"username": "gooduser", "password": "123456"})
    assert login.status_code == 200
    token = login.get_json()["data"]["token"]
    assert token
