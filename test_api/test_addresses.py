"""收货地址簿专项测试
=========================
覆盖点：
- 新增地址成功，且用户的第一条地址自动成为默认地址
- 参数校验：收货人/地区/详细地址非空，手机号正则（11 位大陆手机号）
- 数据隔离：列表里只能看到自己的地址（造第二个用户验证）
- 权限：只能改/删自己的地址，动别人的地址 → 404（不暴露地址存在性）
- 默认地址逻辑：set-default 会把默认标记转移；删默认地址后剩余地址自动有默认
"""
import time
import pytest

from conftest import create_address


def make_user(requests_session, base_url):
    """注册一个全新普通用户，返回 headers"""
    username = f"addr_{int(time.time() * 1000)}"
    requests_session.post(f"{base_url}/register",
                          json={"username": username, "password": "123456"})
    r = requests_session.post(f"{base_url}/login",
                              json={"username": username, "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def list_ids(requests_session, base_url, headers):
    r = requests_session.get(f"{base_url}/addresses", headers=headers)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]


# ---------- 新增 & 第一条自动默认 ----------

def test_create_first_address_auto_default(requests_session, base_url):
    h = make_user(requests_session, base_url)
    r = requests_session.post(f"{base_url}/addresses", headers=h, json={
        "receiver_name": "王五", "receiver_phone": "13700137000",
        "region": "上海市浦东新区", "detail": "张江路88号"
    })
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    aid = r.json()["data"]["id"]
    # 第一条地址自动设为默认
    assert r.json()["data"]["is_default"] == 1

    addrs = list_ids(requests_session, base_url, h)
    target = next(a for a in addrs if a["id"] == aid)
    assert target["is_default"] == 1
    # 默认地址排第一
    assert addrs[0]["id"] == aid


def test_create_address_empty_name_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    r = requests_session.post(f"{base_url}/addresses", headers=h, json={
        "receiver_name": "  ", "receiver_phone": "13700137000",
        "region": "上海市浦东新区", "detail": "张江路88号"
    })
    assert r.status_code == 400
    assert r.json()["code"] == 400


@pytest.mark.parametrize("bad_phone", ["12345678901", "abc"])
def test_create_address_bad_phone_400(requests_session, base_url, bad_phone):
    """手机号正则 ^1[3-9]\\d{9}$：12345678901（非 1[3-9] 开头）和 abc 都非法"""
    h = make_user(requests_session, base_url)
    r = requests_session.post(f"{base_url}/addresses", headers=h, json={
        "receiver_name": "王五", "receiver_phone": bad_phone,
        "region": "上海市浦东新区", "detail": "张江路88号"
    })
    assert r.status_code == 400, r.text


def test_create_address_empty_region_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    r = requests_session.post(f"{base_url}/addresses", headers=h, json={
        "receiver_name": "王五", "receiver_phone": "13700137000",
        "region": "", "detail": "张江路88号"
    })
    assert r.status_code == 400


def test_create_address_empty_detail_400(requests_session, base_url):
    h = make_user(requests_session, base_url)
    r = requests_session.post(f"{base_url}/addresses", headers=h, json={
        "receiver_name": "王五", "receiver_phone": "13700137000",
        "region": "上海市浦东新区", "detail": ""
    })
    assert r.status_code == 400


def test_address_list_only_own(requests_session, base_url):
    """列表隔离：A 的地址列表里不能看到 B 的地址"""
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    a1 = create_address(requests_session, base_url, h1, receiver_name="用户A")
    a2 = create_address(requests_session, base_url, h2, receiver_name="用户B")

    list1 = list_ids(requests_session, base_url, h1)
    ids1 = [a["id"] for a in list1]
    assert a1 in ids1
    assert a2 not in ids1, "地址列表不能包含其他用户的地址"
    assert all(a["receiver_name"] != "用户B" for a in list1)


def test_address_requires_login(requests_session, base_url):
    r = requests_session.get(f"{base_url}/addresses")
    assert r.status_code == 401


# ---------- 修改 ----------

def test_update_own_address_ok(requests_session, base_url):
    h = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h)
    r = requests_session.put(f"{base_url}/addresses/{aid}", headers=h, json={
        "receiver_name": "赵六", "receiver_phone": "13611112222",
        "region": "广东省深圳市南山区", "detail": "科技园路66号"
    })
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    target = next(a for a in list_ids(requests_session, base_url, h) if a["id"] == aid)
    assert target["receiver_name"] == "赵六"
    assert target["receiver_phone"] == "13611112222"
    assert target["region"] == "广东省深圳市南山区"


def test_update_others_address_404(requests_session, base_url):
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h1)

    r = requests_session.put(f"{base_url}/addresses/{aid}", headers=h2, json={
        "receiver_name": "黑客", "receiver_phone": "13500000000",
        "region": "xx省xx市", "detail": "xx路1号"
    })
    assert r.status_code == 404, "改别人的地址应返回 404"


# ---------- 删除 ----------

def test_delete_own_address_ok(requests_session, base_url):
    h = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h)
    r = requests_session.delete(f"{base_url}/addresses/{aid}", headers=h)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    ids = [a["id"] for a in list_ids(requests_session, base_url, h)]
    assert aid not in ids


def test_delete_others_address_404(requests_session, base_url):
    h1 = make_user(requests_session, base_url)
    h2 = make_user(requests_session, base_url)
    aid = create_address(requests_session, base_url, h1)

    r = requests_session.delete(f"{base_url}/addresses/{aid}", headers=h2)
    assert r.status_code == 404, "删别人的地址应返回 404"
    # 地址本体还在
    ids = [a["id"] for a in list_ids(requests_session, base_url, h1)]
    assert aid in ids


# ---------- 默认地址逻辑 ----------

def test_set_default_moves_flag(requests_session, base_url):
    h = make_user(requests_session, base_url)
    first = create_address(requests_session, base_url, h, detail="第一条地址路1号")
    second = create_address(requests_session, base_url, h, detail="第二条地址路2号")

    # 第一条是默认
    addrs = list_ids(requests_session, base_url, h)
    assert next(a for a in addrs if a["id"] == first)["is_default"] == 1

    # 把第二条设为默认
    r = requests_session.post(f"{base_url}/addresses/{second}/set-default", headers=h)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    addrs = list_ids(requests_session, base_url, h)
    assert next(a for a in addrs if a["id"] == second)["is_default"] == 1
    assert next(a for a in addrs if a["id"] == first)["is_default"] == 0
    # 默认地址排第一
    assert addrs[0]["id"] == second


def test_delete_default_auto_assigns_new_default(requests_session, base_url):
    """删除默认地址后，剩余地址中最早创建的一条自动成为默认"""
    h = make_user(requests_session, base_url)
    first = create_address(requests_session, base_url, h, detail="最早地址路1号")
    second = create_address(requests_session, base_url, h, detail="第二条地址路2号")

    r = requests_session.delete(f"{base_url}/addresses/{first}", headers=h)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    addrs = list_ids(requests_session, base_url, h)
    ids = [a["id"] for a in addrs]
    assert first not in ids
    assert second in ids
    # 剩余地址里必须有且仅有一条默认
    defaults = [a for a in addrs if a["is_default"] == 1]
    assert len(defaults) == 1, "删默认地址后应自动补一条默认地址"
    assert defaults[0]["id"] == second
