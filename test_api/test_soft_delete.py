"""软删除（商品下架）专项测试

验证点：
- 下架后顾客端彻底不可见：列表/搜索/详情/加购 全部 404 或查不到
- 后台仍能看到已下架商品，且可以「恢复上架」
- 数据不丢：历史订单、订单明细里的商品名/价格快照不受影响
- 购物车里的已下架商品自动清除
- AI 客服不再推荐/提及已下架商品
- 权限：未登录不能下架/恢复
"""


def admin_headers(requests_session, base_url):
    r = requests_session.post(f"{base_url}/login",
                              json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def make_product(requests_session, base_url, ah, name="软删测试商品", stock=50):
    r = requests_session.post(f"{base_url}/admin/products", headers=ah,
                              json={"name": name, "price": 66.0, "stock": stock,
                                    "description": "软删除测试用"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"]


def delete_product(requests_session, base_url, ah, pid):
    return requests_session.delete(f"{base_url}/admin/products/{pid}", headers=ah)


def restore_product(requests_session, base_url, ah, pid):
    return requests_session.post(f"{base_url}/admin/products/{pid}/restore",
                                 headers=ah)


def customer_products(requests_session, base_url, **params):
    """翻完顾客端商品列表所有分页，返回全部 items（商品列表已分页，
    单页默认 12 条，断言「不在列表」必须翻完全部页才靠谱）"""
    params.setdefault("per_page", 50)
    items, page = [], 1
    while True:
        r = requests_session.get(f"{base_url}/products",
                                 params=dict(params, page=page))
        pd = r.json()["data"]
        items.extend(pd["items"])
        if page >= pd["total_pages"]:
            break
        page += 1
    return items


# ---------- 下架后顾客端不可见 ----------

def test_deleted_product_not_in_list(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    ids = [p["id"] for p in customer_products(requests_session, base_url)]
    assert pid not in ids, "已下架商品不应出现在顾客商品列表"


def test_deleted_product_detail_404(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    r = requests_session.get(f"{base_url}/products/{pid}")
    assert r.status_code == 404, "已下架商品详情应对顾客返回 404"


def test_deleted_product_cannot_add_cart(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    r = requests_session.post(f"{base_url}/cart", headers=auth_headers,
                              json={"product_id": pid, "quantity": 1})
    assert r.status_code == 404, "已下架商品不能加入购物车"


def test_keyword_search_excludes_deleted(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    keyword = "绝版软删书"
    pid = make_product(requests_session, base_url, ah, name=keyword)
    # 删之前能搜到
    hits = customer_products(requests_session, base_url, keyword=keyword)
    assert any(p["id"] == pid for p in hits)
    # 删之后搜不到
    delete_product(requests_session, base_url, ah, pid)
    hits = customer_products(requests_session, base_url, keyword=keyword)
    assert all(p["id"] != pid for p in hits), "搜索结果不应包含已下架商品"


# ---------- 后台视角：能看到、能恢复 ----------

def test_admin_list_shows_deleted_with_flag(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    # 后台商品列表已分页：翻完全部页找到目标商品，is_deleted 标记必须为 1
    r = requests_session.get(f"{base_url}/admin/products",
                             params={"per_page": 50}, headers=ah)
    assert r.status_code == 200
    pd = r.json()["data"]
    target = next((p for p in pd["items"] if p["id"] == pid), None)
    if target is None and pd["total_pages"] > 1:
        # 极端情况下目标商品不在第 1 页：继续翻页找
        for page in range(2, pd["total_pages"] + 1):
            rows = requests_session.get(f"{base_url}/admin/products",
                                        params={"page": page, "per_page": 50},
                                        headers=ah).json()["data"]["items"]
            target = next((p for p in rows if p["id"] == pid), None)
            if target:
                break
    assert target is not None, "后台列表应包含已下架商品"
    assert target["is_deleted"] == 1, "后台列表应包含已下架商品且标记 is_deleted=1"


def test_restore_product_visible_again(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    r = restore_product(requests_session, base_url, ah, pid)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text

    # 恢复后顾客端重新可见、可买
    r = requests_session.get(f"{base_url}/products/{pid}")
    assert r.status_code == 200
    r = requests_session.post(f"{base_url}/cart", headers=auth_headers,
                              json={"product_id": pid, "quantity": 1})
    assert r.status_code == 200, "恢复上架后应能正常加购"


def test_double_delete_rejected(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)
    r = delete_product(requests_session, base_url, ah, pid)
    assert r.status_code == 400, "重复下架应被拒绝"


def test_restore_on_sale_rejected(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    r = restore_product(requests_session, base_url, ah, pid)
    assert r.status_code == 400, "在售商品无需恢复，应返回 400"


def test_update_deleted_product_rejected(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    delete_product(requests_session, base_url, ah, pid)

    r = requests_session.put(f"{base_url}/admin/products/{pid}", headers=ah,
                             json={"price": 1.0})
    assert r.status_code == 400, "已下架商品必须先恢复才能修改"


# ---------- 数据不丢：订单/购物车 ----------

def test_order_history_kept_after_delete(auth_headers, requests_session, base_url, user_address_id):
    """核心价值：商品下架后，历史订单和订单明细快照必须完整可查"""
    ah = admin_headers(requests_session, base_url)
    name = "下单后下架纪念款"
    pid = make_product(requests_session, base_url, ah, name=name, stock=50)

    # 顾客：加购 → 下单 → 支付 → 模拟回调
    requests_session.post(f"{base_url}/cart", headers=auth_headers,
                          json={"product_id": pid, "quantity": 1})
    r = requests_session.post(f"{base_url}/orders", headers=auth_headers,
                              json={"address_id": user_address_id})
    oid = r.json()["data"]["order_id"]
    requests_session.post(f"{base_url}/orders/{oid}/pay", headers=auth_headers,
                          json={"channel": "wechat"})
    requests_session.post(f"{base_url}/pay/mock-notify", json={"order_id": oid})

    # 管理员下架商品
    delete_product(requests_session, base_url, ah, pid)

    # 订单还在，明细里的商品名/价格快照还在
    r = requests_session.get(f"{base_url}/orders", headers=auth_headers)
    order = next(o for o in r.json()["data"]["items"] if o["id"] == oid)
    assert order["status"] == "paid"
    # 注意：测试用户整个 pytest 会话共享（conftest 的 register_user 是 session 级），
    # 购物车里可能残留其他用例加购的商品，下单会结算购物车全部商品，
    # 因此不能假设 items[0] 就是本用例的商品——按唯一商品名精确锁定自己造的明细，
    # 再校验名/价快照（共享状态下断言只锁定自己的数据，是用例隔离的正确姿势）
    item = next((i for i in order["items"] if i["product_name"] == name), None)
    assert item is not None, "历史订单中应保留下单时的商品明细"
    assert item["price"] == 66.0, "订单明细应保留下单时的价格快照"


def test_cart_auto_clears_after_delete(auth_headers, requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    pid = make_product(requests_session, base_url, ah)
    requests_session.post(f"{base_url}/cart", headers=auth_headers,
                          json={"product_id": pid, "quantity": 2})

    delete_product(requests_session, base_url, ah, pid)

    r = requests_session.get(f"{base_url}/cart", headers=auth_headers)
    pids = [i["product_id"] for i in r.json()["data"]["items"]]
    assert pid not in pids, "商品下架后购物车里的该项应自动清除"


# ---------- AI 客服不提及已下架商品 ----------

def test_ai_chat_excludes_deleted(requests_session, base_url):
    ah = admin_headers(requests_session, base_url)
    name = "绝版画册XYZ"
    pid = make_product(requests_session, base_url, ah, name=name)

    # 下架前客服能查到
    r = requests_session.post(f"{base_url}/ai/chat", json={"message": f"{name}多少钱"})
    assert any(p["id"] == pid for p in r.json()["data"]["products"])

    delete_product(requests_session, base_url, ah, pid)

    # 下架后客服不再推荐/提及
    r = requests_session.post(f"{base_url}/ai/chat", json={"message": f"{name}多少钱"})
    assert all(p["id"] != pid for p in r.json()["data"]["products"]), \
        "AI 客服不应返回已下架商品"


# ---------- 权限 ----------

def test_anonymous_cannot_delete_or_restore(requests_session, base_url):
    r = requests_session.delete(f"{base_url}/admin/products/1")
    assert r.status_code == 401
    r = requests_session.post(f"{base_url}/admin/products/1/restore")
    assert r.status_code == 401
