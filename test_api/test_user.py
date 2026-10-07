"""用户注册登录测试"""
import time


def test_register_success(requests_session, base_url):
    username = f"newuser_{int(time.time())}"
    resp = requests_session.post(f"{base_url}/register",
                                 json={"username": username, "password": "123456"})
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_register_short_password(requests_session, base_url):
    username = f"short_{int(time.time())}"
    resp = requests_session.post(f"{base_url}/register",
                                 json={"username": username, "password": "123"})
    assert resp.status_code == 400
    assert "密码" in resp.json()["msg"]


def test_register_duplicate(requests_session, base_url, register_user):
    # register_user fixture 已经注册了一个用户，重复注册应失败
    user_id, token = register_user
    # 取这个用户名需要重新查；这里直接用固定账号演示重复逻辑
    # 为了用例独立，先注册一个固定名字，再注册一次
    name = f"dup_{int(time.time())}"
    requests_session.post(f"{base_url}/register",
                          json={"username": name, "password": "123456"})
    resp = requests_session.post(f"{base_url}/register",
                                 json={"username": name, "password": "123456"})
    assert resp.status_code == 400
    assert "已存在" in resp.json()["msg"]


def test_login_wrong_password(requests_session, base_url):
    resp = requests_session.post(f"{base_url}/login",
                                 json={"username": "nouser", "password": "wrongpass"})
    assert resp.status_code == 401


def test_login_success(register_user, requests_session, base_url):
    user_id, token = register_user
    # token 已经在 fixture 里通过登录拿到，这里验证它非空
    assert token
