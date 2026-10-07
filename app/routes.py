import re
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g, current_app, session
from sqlalchemy import or_, text
from app import db
from app.models import (User, Product, Cart, Order, OrderItem, Address,
                        LogisticsTrack, AfterSale, Review)
from app.payment import create_payment, handle_paid
from app.aftersale_ai import analyze_aftersale, SUGGESTION_TEXT
from app.dashboard_brief import generate_briefing
from app.review_ai import aggregate_review_tags
from app.rfm_analysis import build_rfm
from app.security import issue_token, parse_token

api_bp = Blueprint("api", __name__)


def _secret_key():
    """签名 token 的密钥：优先用应用 SECRET_KEY；未配置则用固定兜底，
    保证老 config（无 SECRET_KEY）也能跑。生产环境务必在 config 设置 SECRET_KEY。"""
    return current_app.config.get("SECRET_KEY") or "shop_demo_demo_secret_2026"


def _auth_user_id():
    """从 Authorization 头解析签名 token，返回 user 或 None。
    取代早期『token 即 user_id』——伪造/篡改/过期一律无效。"""
    token = request.headers.get("Authorization", "")
    if not token:
        return None
    token = token.replace("Bearer ", "", 1).strip()
    uid = parse_token(token, _secret_key())
    if uid is None:
        return None
    return User.query.get(uid)


def product_images(image):
    """把 image 字段解析成图片数组：image 允许存英文逗号分隔的多个文件名
    （如 "a.png,b.png"），按逗号 split 后去空格、去空串；
    为空（没传图）返回 ["default.svg"]，前端轮播图直接用。"""
    names = [s.strip() for s in (image or "").split(",")]
    names = [s for s in names if s]
    return names or ["default.svg"]


def product_json(p):
    """商品的统一输出格式（顾客端和管理端共用）"""
    return {
        "id": p.id, "name": p.name, "price": p.price,
        "stock": p.stock, "description": p.description,
        "image": p.image or "default.svg",  # 兜底：没传图的商品统一显示占位图
        # 多图轮播：image 字段可存逗号分隔的多个文件名，images 是解析后的数组
        # （image 单字段保留返回，兼容老前端/老用例）
        "images": product_images(p.image),
        # 有效评价汇总（avg_rating/count/good_rate/rating_dist；
        # 无评价时结构恒定：avg_rating=null、count/good_rate=0、分布全 0）
        # 说明：这里逐商品聚合，商品列表存在 N+1 查询；毕设规模（数十件商品/每页
        # 12 条）完全够用，后续可优化为一次 GROUP BY 批量聚合后按 product_id 映射
        "rating_summary": product_rating_summary(p.id),
    }


# ------------ Token 认证（HMAC 签名 token，见 app/security.py）------------
def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = _auth_user_id()
        if not user:
            return jsonify({"code": 401, "msg": "未登录或登录已失效"}), 401
        g.current_user = user
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    """要求登录且是管理员（未登录/失效 401，普通顾客 403）"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = _auth_user_id()
        if not user:
            return jsonify({"code": 401, "msg": "未登录或登录已失效"}), 401
        if not user.is_admin:
            return jsonify({"code": 403, "msg": "需要管理员权限"}), 403
        g.current_user = user
        return func(*args, **kwargs)
    return wrapper


def optional_user():
    """从请求头解析当前用户（未登录/ token 无效返回 None，不报错拦截）。
    AI 客服/推荐这类接口游客也能用，登录了则提供个性化能力。"""
    return _auth_user_id()


def aftersale_json(a):
    """售后单的统一输出格式（顾客端订单详情 / 后台售后列表共用）。
    注意 admin_note 为空串/None 时统一给 null，前端判断更简单。"""
    return {
        "id": a.id,
        "order_id": a.order_id,
        "type": a.type,
        "type_text": AfterSale.TYPE_TEXT.get(a.type, a.type),
        "reason": a.reason,
        "status": a.status,
        "status_text": AfterSale.STATUS_TEXT.get(a.status, a.status),
        "admin_note": (a.admin_note or "").strip() or None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "handled_at": a.handled_at.isoformat() if a.handled_at else None,
        # 智能初审建议（只作参考，状态变更唯一入口仍是管理员 handle）：
        # 老售后单没有分析过 → 三个字段均为 null
        "ai_suggestion": a.ai_suggestion,
        "ai_suggestion_text": SUGGESTION_TEXT.get(a.ai_suggestion) if a.ai_suggestion else None,
        "ai_reason": a.ai_reason,
        "ai_analyzed_at": a.ai_analyzed_at.isoformat() if a.ai_analyzed_at else None,
    }


def order_json(o):
    """订单的统一输出格式（顾客端和管理端共用）"""
    return {
        "id": o.id,
        "user_id": o.user_id,
        "username": o.user.username if o.user else "",
        "total_amount": o.total_amount,
        "status": o.status,
        "status_text": Order.STATUS.get(o.status, o.status),
        "actions": Order.ACTIONS.get(o.status, []),  # 当前状态允许的操作
        "pay_channel": o.pay_channel,                 # 支付渠道 wechat/alipay
        "trade_no": o.trade_no,                       # 支付平台流水号
        "pay_closed": o.pay_closed or 0,              # 支付单是否已主动关闭
        "pay_closed_at": o.pay_closed_at.isoformat() if o.pay_closed_at else None,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        # 是否已评价（订单级一对一；前端 completed 订单据此显示评价按钮或星级）
        "reviewed": o.review is not None,
        # 收货地址快照（下单时从地址簿拷贝，历史订单不受地址簿改/删影响）
        "address_snapshot": parse_address_snapshot(o.address_snapshot),
        "logistics_company": o.logistics_company or "",   # 物流公司
        "tracking_no": o.tracking_no or "",                # 运单号
        "shipped_at": o.shipped_at.isoformat() if o.shipped_at else None,  # 发货时间
        # 售后单：返回最新一张（rejected 后重新申请时旧单保留为历史，
        # 前端按钮/状态条只认最新单）；从未申请过为 null
        "aftersale": aftersale_json(o.latest_aftersale) if o.latest_aftersale else None,
        "created_at": o.created_at.isoformat(),
        "items": [{
            "product_name": it.product_name,
            "price": it.price,
            "quantity": it.quantity
        } for it in o.items]
    }


def restore_stock(order):
    """把订单里扣掉的库存还回去（取消订单时调用）"""
    for it in order.items:
        product = Product.query.get(it.product_id)
        if product and not product.is_deleted:
            product.stock += it.quantity


# 待支付订单超时时间：下单后超过该时长仍未支付 → 自动取消并释放库存
PAY_TIMEOUT = timedelta(minutes=30)


def expire_order_if_needed(order):
    """惰性超时关单：若该待支付订单已超过 PAY_TIMEOUT，则取消、回补库存并落库，返回 True。
    在订单查询/支付/操作等入口前置调用；非 pending 单或未超时返回 False。"""
    if order is None or order.status != "pending":
        return False
    created = order.created_at or datetime.utcnow()
    if datetime.utcnow() - created > PAY_TIMEOUT:
        order.status = "cancelled"
        restore_stock(order)
        db.session.commit()
        return True
    return False


# ------------ 评价（订单级，收货后评价） ------------
def mask_username(username):
    """买家用户名脱敏：只保留首字，其余用 ** 代替（如「张三丰」→「张**」）。
    公开评价列表对游客可见，不能泄露买家用户名全称。"""
    if not username:
        return "匿名用户"
    return username[0] + "**"


def order_product_names(order):
    """聚合一笔订单里所有商品名快照（评价展示用，如 ["机械键盘", "无线鼠标"]）。
    取 OrderItem.product_name 下单快照而非商品表现名——商品之后改名/下架，
    历史评价展示的仍是顾客当时买到的东西。"""
    return [it.product_name for it in sorted(order.items, key=lambda x: x.id)]


def review_json(r):
    """评价的统一输出格式（顾客查自己的评价 / 商品公开评价列表 / 后台共用）。
    公开场景用户名脱敏；购买商品名按订单明细聚合。"""
    return {
        "id": r.id,
        "order_id": r.order_id,
        "user_id": r.user_id,
        "rating": r.rating,
        "content": r.content,
        "is_deleted": r.is_deleted or 0,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "username": mask_username(r.user.username if r.user else ""),
        "product_names": order_product_names(r.order) if r.order else [],
    }


def product_review_query(pid):
    """某商品的全部【有效】评价查询（is_deleted=0）。
    路径：OrderItem.product_id==该商品 → 其订单 → 订单的 Review。
    去重用 EXISTS 子查询（Order.items.any 会被 SQLAlchemy 编译成相关 EXISTS），
    同一订单买 2 件同一商品时 Review 只出现一次；MySQL/SQLite 行为一致，
    不依赖 DISTINCT 在特定方言上的排序限制。"""
    return (Review.query
            .join(Order, Review.order_id == Order.id)
            .filter(Order.items.any(OrderItem.product_id == pid),
                    Review.is_deleted == 0)
            .order_by(Review.created_at.desc(), Review.id.desc()))


def product_rating_summary(pid):
    """某商品有效评价汇总（与分页无关，始终统计全部有效评价）：
    {avg_rating: 四舍五入保留1位小数或None, count: 有效评价数,
     good_rate: 4-5星占比百分比整数, rating_dist: {1..5: 条数}}；
    无评价时 avg_rating 为 None、count/good_rate 为 0、分布全 0
    （结构恒定，前端不必判空；用 count==0 区分「暂无评价」）。

    说明：product_json 列表场景每商品调用一次，存在 N+1 查询；毕设规模 5~20 件
    商品完全可接受。上量后可改为一次 GROUP BY product_id 聚合 + 内存分组的
    批量函数 product_rating_summary_map(product_ids)，接口签名不变即可替换。"""
    ratings = [row[0] for row in product_review_query(pid)
               .with_entities(Review.rating).all()]
    dist = {star: 0 for star in range(Review.RATING_MIN, Review.RATING_MAX + 1)}
    for star in ratings:
        if star in dist:
            dist[star] += 1
    count = len(ratings)
    if not count:
        return {"avg_rating": None, "count": 0, "good_rate": 0,
                "rating_dist": dist}
    good = sum(c for star, c in dist.items() if star >= 4)
    return {
        "avg_rating": round(sum(ratings) / count, 1),
        "count": count,
        "good_rate": round(good * 100 / count),
        "rating_dist": dist,
    }


def review_keyword_clause(keyword):
    """后台评价列表的关键字搜索条件（照售后搜索 clause 风格，三条件 OR）：
    - 模糊匹配评价内容；- 模糊匹配评价人用户名；
    - 去 # 后纯数字按订单号(Review.order_id)精确匹配。"""
    key = keyword.lstrip("#").strip()
    conds = [
        Review.content.like(f"%{key}%"),
        Review.user.has(User.username.like(f"%{key}%")),
    ]
    if key.isdigit():
        conds.append(Review.order_id == int(key))
    return or_(*conds)


# ------------ 商品字段校验工具 ------------
def parse_int_field(value):
    """严格解析整数：返回 (int值, None)；非法（字母/小数/None/布尔）返回 (None, 错误)。
    不能直接 int(value)——int(1.5) 会静默截断成 1，库存 1.5 件是非法输入。"""
    if isinstance(value, bool) or value is None:
        return None, "必须是整数"
    if isinstance(value, float):
        if value.is_integer():
            value = int(value)
        else:
            return None, "必须是整数"
    try:
        text = str(value).strip()
        n = int(text)
    except (TypeError, ValueError):
        return None, "必须是整数"
    return n, None


# ------------ 用户 ------------
@api_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"code": 400, "msg": "用户名和密码不能为空"}), 400
    if len(username) < 3:
        return jsonify({"code": 400, "msg": "用户名至少3位"}), 400
    if len(password) < 6:
        return jsonify({"code": 400, "msg": "密码至少6位"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"code": 400, "msg": "用户名已存在"}), 400

    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({"code": 0, "msg": "注册成功", "data": {"user_id": user.id}})


@api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({"code": 401, "msg": "用户名或密码错误"}), 401

    # 老明文密码登录成功后【透明升级】为哈希（迁移过渡期，逐次登录即完成升级）
    from app.security import is_hashed
    if not is_hashed(user.password):
        user.set_password(password)

    # 同步建立页面登录态（Flask session cookie）：供 /admin 等页面做服务端鉴权，
    # 页面请求由浏览器自动携带 cookie，无需前端改动
    session.clear()
    session["user_id"] = user.id
    session["is_admin"] = bool(user.is_admin)

    # 签发 HMAC 签名 token（不再是可伪造的 user_id）
    token = issue_token(user.id, _secret_key())
    return jsonify({
        "code": 0, "msg": "登录成功",
        "data": {
            "user_id": user.id, "username": user.username,
            "token": token, "is_admin": bool(user.is_admin)
        }
    })


# ------------ 商品（顾客端） ------------
@api_bp.route("/products", methods=["GET"])
def list_products():
    """顾客端商品列表（分页 + 关键字搜索）：
    GET /api/products?page=1&per_page=12&keyword=键盘
    返回统一分页结构 data: {items, total, page, per_page, total_pages}
    （默认每页 12 件，per_page 上限 50；已下架商品不返回）"""
    parsed, err = get_page_params(default_size=12)
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed
    q = Product.query.filter_by(is_deleted=0)
    keyword = request.args.get("keyword", "")
    if keyword:
        q = q.filter(Product.name.like(f"%{keyword}%"))
    q = q.order_by(Product.id.asc())
    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, product_json)})


@api_bp.route("/products/<int:pid>", methods=["GET"])
def get_product(pid):
    p = Product.query.get(pid)
    if not p or p.is_deleted:
        return jsonify({"code": 404, "msg": "商品不存在"}), 404
    return jsonify({"code": 0, "data": product_json(p)})


# ------------ 购物车 ------------
@api_bp.route("/cart", methods=["GET"])
@login_required
def list_cart():
    items = Cart.query.filter_by(user_id=g.current_user.id).all()
    # 已下架商品自动从购物车移除（软删除后购物车里的残留项数据自愈）
    gone = [i for i in items if not i.product or i.product.is_deleted]
    for i in gone:
        db.session.delete(i)
    if gone:
        db.session.commit()
        items = [i for i in items if i not in gone]
    total = sum(i.product.price * i.quantity for i in items)
    return jsonify({"code": 0, "data": {
        "items": [{
            "id": i.id, "product_id": i.product_id,
            "product_name": i.product.name, "price": i.product.price,
            "quantity": i.quantity, "subtotal": i.product.price * i.quantity,
            # 多图商品 image 字段是逗号分隔串，购物车缩略图只取第一张（product_images 自带兜底）
            "image": product_images(i.product.image)[0]
        } for i in items],
        "total": total
    }})


@api_bp.route("/cart", methods=["POST"])
@login_required
def add_cart():
    data = request.get_json(silent=True) or {}
    pid = data.get("product_id")
    # quantity 严格校验：字母/小数/None/布尔 → 400，不能直接 int()（抛 ValueError 会变 500）
    quantity, qty_err = parse_int_field(data.get("quantity", 1))
    if qty_err:
        return jsonify({"code": 400, "msg": "商品数量必须是整数"}), 400

    product = Product.query.get(pid)
    if not product or product.is_deleted:
        return jsonify({"code": 404, "msg": "商品不存在或已下架"}), 404
    if quantity <= 0:
        return jsonify({"code": 400, "msg": "数量必须大于0"}), 400
    if product.stock < quantity:
        return jsonify({"code": 400, "msg": f"库存不足，仅剩{product.stock}件"}), 400

    item = Cart.query.filter_by(user_id=g.current_user.id, product_id=pid).first()
    if item:
        item.quantity += quantity
    else:
        item = Cart(user_id=g.current_user.id, product_id=pid, quantity=quantity)
        db.session.add(item)
    db.session.commit()
    return jsonify({"code": 0, "msg": "已加入购物车"})


@api_bp.route("/cart/<int:item_id>", methods=["PUT"])
@login_required
def update_cart(item_id):
    data = request.get_json(silent=True) or {}
    # quantity 严格校验（含 PUT 改数量）：非法输入返回 400，不再抛 ValueError 变 500
    quantity, qty_err = parse_int_field(data.get("quantity", 0))
    if qty_err:
        return jsonify({"code": 400, "msg": "商品数量必须是整数"}), 400

    item = Cart.query.filter_by(id=item_id, user_id=g.current_user.id).first()
    if not item:
        return jsonify({"code": 404, "msg": "购物车项不存在"}), 404
    if quantity <= 0:
        db.session.delete(item)
        db.session.commit()
        return jsonify({"code": 0, "msg": "已从购物车移除"})
    if item.product.stock < quantity:
        return jsonify({"code": 400, "msg": f"库存不足，仅剩{item.product.stock}件"}), 400

    item.quantity = quantity
    db.session.commit()
    return jsonify({"code": 0, "msg": "数量已更新"})


@api_bp.route("/cart/<int:item_id>", methods=["DELETE"])
@login_required
def delete_cart(item_id):
    item = Cart.query.filter_by(id=item_id, user_id=g.current_user.id).first()
    if not item:
        return jsonify({"code": 404, "msg": "购物车项不存在"}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"code": 0, "msg": "已删除"})


# ------------ 收货地址簿 ------------
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def address_json(a):
    """地址的统一输出格式"""
    return {
        "id": a.id,
        "receiver_name": a.receiver_name,
        "receiver_phone": a.receiver_phone,
        "region": a.region,
        "detail": a.detail,
        "is_default": a.is_default,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def parse_address_snapshot(raw):
    """解析订单上的地址快照 JSON；老订单/没快照的返回 None"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def validate_address_payload(data):
    """校验地址表单，返回 (values_dict, err_msg)。校验通过 err_msg 为 None。"""
    receiver_name = (data.get("receiver_name") or "").strip()
    receiver_phone = (data.get("receiver_phone") or "").strip()
    region = (data.get("region") or "").strip()
    detail = (data.get("detail") or "").strip()
    if not receiver_name:
        return None, "收货人不能为空"
    if not PHONE_RE.match(receiver_phone):
        return None, "手机号格式不正确（请输入11位大陆手机号）"
    if not region:
        return None, "所在地区不能为空"
    if not detail:
        return None, "详细地址不能为空"
    return {
        "receiver_name": receiver_name,
        "receiver_phone": receiver_phone,
        "region": region,
        "detail": detail,
    }, None


def clear_default_addresses(user_id, exclude_id=None):
    """把该用户其它地址的默认标记取消（同一事务内调用）。"""
    q = Address.query.filter_by(user_id=user_id, is_default=1)
    if exclude_id is not None:
        q = q.filter(Address.id != exclude_id)
    for a in q.all():
        a.is_default = 0


@api_bp.route("/addresses", methods=["GET"])
@login_required
def list_addresses():
    """我的收货地址：默认地址排第一，其余按创建时间倒序"""
    rows = Address.query.filter_by(user_id=g.current_user.id).all()
    rows.sort(key=lambda a: (a.is_default != 1,
                             -(a.created_at.timestamp() if a.created_at else 0),
                             -a.id))
    return jsonify({"code": 0, "data": [address_json(a) for a in rows]})


@api_bp.route("/addresses", methods=["POST"])
@login_required
def create_address():
    """新增收货地址。用户的第一条地址自动设为默认；is_default=true 时
    把该用户其它地址默认取消（同一事务）。"""
    data = request.get_json(silent=True) or {}
    values, err = validate_address_payload(data)
    if err:
        return jsonify({"code": 400, "msg": err}), 400

    has_address = Address.query.filter_by(user_id=g.current_user.id).first()
    is_default = 1 if (not has_address or data.get("is_default")) else 0
    if is_default:
        clear_default_addresses(g.current_user.id)
    addr = Address(user_id=g.current_user.id, is_default=is_default, **values)
    db.session.add(addr)
    db.session.commit()
    return jsonify({"code": 0, "msg": "地址已添加",
                    "data": {"id": addr.id, "is_default": addr.is_default}})


@api_bp.route("/addresses/<int:aid>", methods=["PUT"])
@login_required
def update_address(aid):
    """修改收货地址：只能改自己的（访问他人地址返回 404）"""
    data = request.get_json(silent=True) or {}
    addr = Address.query.filter_by(id=aid, user_id=g.current_user.id).first()
    if not addr:
        return jsonify({"code": 404, "msg": "地址不存在"}), 404
    values, err = validate_address_payload(data)
    if err:
        return jsonify({"code": 400, "msg": err}), 400

    addr.receiver_name = values["receiver_name"]
    addr.receiver_phone = values["receiver_phone"]
    addr.region = values["region"]
    addr.detail = values["detail"]
    if data.get("is_default"):
        clear_default_addresses(g.current_user.id)
        addr.is_default = 1
    db.session.commit()
    return jsonify({"code": 0, "msg": "地址已更新"})


@api_bp.route("/addresses/<int:aid>", methods=["DELETE"])
@login_required
def delete_address(aid):
    """删除收货地址：只能删自己的（404）。删除默认地址后，
    自动把剩余地址中最早创建的一条设为默认。"""
    addr = Address.query.filter_by(id=aid, user_id=g.current_user.id).first()
    if not addr:
        return jsonify({"code": 404, "msg": "地址不存在"}), 404

    was_default = addr.is_default == 1
    db.session.delete(addr)
    db.session.flush()
    if was_default:
        fallback = Address.query.filter_by(user_id=g.current_user.id)\
            .order_by(Address.created_at.asc(), Address.id.asc()).first()
        if fallback:
            fallback.is_default = 1
    db.session.commit()
    return jsonify({"code": 0, "msg": "地址已删除"})


@api_bp.route("/addresses/<int:aid>/set-default", methods=["POST"])
@login_required
def set_default_address(aid):
    """把该地址设为默认、其它地址取消默认：只能操作自己的（404）"""
    addr = Address.query.filter_by(id=aid, user_id=g.current_user.id).first()
    if not addr:
        return jsonify({"code": 404, "msg": "地址不存在"}), 404
    clear_default_addresses(g.current_user.id)
    addr.is_default = 1
    db.session.commit()
    return jsonify({"code": 0, "msg": "已设为默认地址"})


# ------------ 订单（顾客端） ------------
@api_bp.route("/orders", methods=["POST"])
@login_required
def create_order():
    data = request.get_json(silent=True) or {}
    all_items = Cart.query.filter_by(user_id=g.current_user.id).all()
    if not all_items:
        return jsonify({"code": 400, "msg": "购物车是空的"}), 400

    # ---- 勾选结算：cart_item_ids 为【勾选的购物车条目 id 数组】----
    # 不传该字段（键不存在或值为 null）→ 维持老行为：整辆购物车下单；
    # 传 [] 空数组 → 400；传非空数组 → 只结算勾选项，未勾选条目原样保留。
    raw_ids = data.get("cart_item_ids", None)
    if raw_ids is None:
        items = all_items  # 向后兼容：旧前端/旧测试零改动
    else:
        # 类型校验：必须是数组，且元素必须是整数（bool 是 int 子类，下单 id 不能是 true/false）
        if not isinstance(raw_ids, list) or any(
                not isinstance(x, int) or isinstance(x, bool) for x in raw_ids):
            return jsonify({"code": 400,
                            "msg": "cart_item_ids 必须是整数数组"}), 400
        if not raw_ids:
            return jsonify({"code": 400, "msg": "请勾选要结算的商品"}), 400
        # 越权校验：先取集合差集——夹带别人的/不存在的 id 一律拒绝，
        # 在校验通过前绝不建单、绝不扣库存
        own_ids = {i.id for i in all_items}
        if not set(raw_ids).issubset(own_ids):
            return jsonify({"code": 400, "msg": "购物车商品不存在"}), 400
        # 按数组里的勾选顺序取条目（重复 id 去重，防止重复结算）
        items = [i for i in all_items if i.id in set(raw_ids)]
        if not items:
            return jsonify({"code": 400, "msg": "请勾选要结算的商品"}), 400

    # 收货地址必选：校验存在且属于当前用户（越权用别人的地址 → 404）
    address_id = data.get("address_id")
    if not address_id:
        return jsonify({"code": 400, "msg": "请选择收货地址"}), 400
    address = Address.query.filter_by(id=address_id,
                                      user_id=g.current_user.id).first()
    if not address:
        return jsonify({"code": 404, "msg": "收货地址不存在"}), 404

    total = sum(i.product.price * i.quantity for i in items)
    # 地址内容拷贝为 JSON 快照：之后地址簿改/删都不影响历史订单
    snapshot = json.dumps({
        "receiver_name": address.receiver_name,
        "receiver_phone": address.receiver_phone,
        "region": address.region,
        "detail": address.detail,
    }, ensure_ascii=False)
    order = Order(user_id=g.current_user.id, total_amount=total, status="pending",
                  address_snapshot=snapshot)
    db.session.add(order)
    db.session.flush()

    for i in items:
        if not i.product or i.product.is_deleted:
            db.session.rollback()
            return jsonify({"code": 400, "msg": f"商品[{i.product_id}]已下架，请刷新购物车"}), 400
        if i.product.stock < i.quantity:
            db.session.rollback()
            return jsonify({"code": 400, "msg": f"{i.product.name}库存不足"}), 400
        db.session.add(OrderItem(
            order_id=order.id, product_id=i.product_id,
            product_name=i.product.name, price=i.product.price,
            quantity=i.quantity
        ))
        i.product.stock -= i.quantity
        db.session.delete(i)

    db.session.commit()
    return jsonify({"code": 0, "msg": "下单成功",
                    "data": {"order_id": order.id, "total": total,
                             "status": "pending", "status_text": "待支付"}})


# ------------ 分页工具 ------------
def get_page_params(default_size=10):
    """从 querystring 解析分页参数 page / per_page。
    非法参数返回 (None, 错误消息)，供接口返回 400。"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", default_size))
    except (TypeError, ValueError):
        return None, "page和per_page必须是正整数"
    if page < 1 or per_page < 1:
        return None, "page和per_page必须是正整数"
    if per_page > 50:
        return None, "per_page最大50"
    return (page, per_page), None


def paginate_data(query, page, per_page, item_fn):
    """对 query 做分页查询，返回统一分页结构。
    页码越界不报错，返回空 items（total_pages=0），前端显示空状态。"""
    total = query.count()
    total_pages = (total + per_page - 1) // per_page if total else 0
    items = query.limit(per_page).offset((page - 1) * per_page).all()
    return {
        "items": [item_fn(o) for o in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def get_keyword():
    """统一读取 keyword 查询参数：去首尾空格，空串等同未传（返回 ""）。"""
    return (request.args.get("keyword", "") or "").strip()


def order_keyword_clause(keyword):
    """订单列表的关键字搜索条件（后台全部订单 / 我的订单共用）：
    - 允许带前导 #（前端订单号显示为 #id），去掉后纯数字按订单 id 精确匹配；
    - 同时模糊匹配订单内商品名快照（OrderItem.product_name）；
    - 两个条件 OR。非数字关键字只有商品名条件。
    全部走 SQLAlchemy 表达式参数化，禁止拼 SQL 字符串。"""
    key = keyword.lstrip("#").strip()
    conds = [Order.items.any(OrderItem.product_name.like(f"%{key}%"))]
    if key.isdigit():
        conds.append(Order.id == int(key))
    return or_(*conds)


def aftersale_keyword_clause(keyword):
    """售后单列表的关键字搜索条件：
    - 去 # 后纯数字按订单号(AfterSale.order_id)精确匹配；
    - 模糊匹配申请原因 reason；
    - 模糊匹配申请人用户名（AfterSale.user → User.username）。
    三个条件 OR，与外部 status 筛选 AND 组合。"""
    key = keyword.lstrip("#").strip()
    conds = [
        AfterSale.reason.like(f"%{key}%"),
        AfterSale.user.has(User.username.like(f"%{key}%")),
    ]
    if key.isdigit():
        conds.append(AfterSale.order_id == int(key))
    return or_(*conds)


@api_bp.route("/orders", methods=["GET"])
@login_required
def list_orders():
    """我的订单（分页 + 关键字搜索）：
    GET /api/orders?page=1&per_page=10&keyword=键盘
    keyword 匹配订单号（支持 #123 写法）或订单内商品名快照，只搜本人订单。
    返回统一分页结构 data: {items, total, page, per_page, total_pages}"""
    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed
    q = Order.query.filter_by(user_id=g.current_user.id)
    # 关键字：订单号（支持 #123 写法）或订单内商品名快照；始终限定本人订单
    keyword = get_keyword()
    if keyword:
        q = q.filter(order_keyword_clause(keyword))
    q = q.order_by(Order.created_at.desc(), Order.id.desc())
    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, order_json)})


@api_bp.route("/orders/<int:oid>/action", methods=["POST"])
@login_required
def order_action(oid):
    """顾客操作自己的订单。body: {"action": "pay" | "confirm" | "cancel"}
    - pay    付款：待支付 → 待发货（兼容旧入口，内部走模拟支付网关）
    - confirm 确认收货：已发货 → 已完成（物流轨迹在发货时已生成完整链路，
      确认收货只改状态，不再追加轨迹；售后审核中(pending)/已同意(approved)
      的订单拦截不允许确认收货，售后被拒绝(rejected)不拦截）
    - cancel 取消：待支付/待发货 → 已取消（库存退回去）
    """
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    # 惰性超时关单：打开订单操作入口时，先把已超时的待支付单作废
    if expire_order_if_needed(order):
        return jsonify({"code": 400,
                        "msg": "订单超过30分钟未支付，已自动取消并释放库存"}), 400

    allowed = Order.ACTIONS.get(order.status, [])
    if action not in allowed:
        return jsonify({
            "code": 400,
            "msg": f"当前状态[{Order.STATUS[order.status]}]不允许此操作"
        }), 400

    if action == "pay":
        # 走支付网关：先下单登记，再模拟"支付平台回调"完成付款
        channel = (data.get("channel") or "wechat").lower()
        pay_info, err = create_payment(order, channel)
        if err:
            return jsonify({"code": 400, "msg": err}), 400
        handle_paid(order, pay_info["trade_no"])
        msg = f"{pay_info['channel_name']}支付成功，等待商家发货"
    elif action == "confirm":
        # 确认收货只改订单状态：物流轨迹发货时已生成「已签收」完整链路，
        # 这里不重复写轨迹（completed 后不能再 confirm，天然幂等）。
        # 售后双保险：前端在售后 pending/approved 时已隐藏确认收货按钮，
        # 后端仍要拦截——pending 等审核结果，approved 售后已同意无需再确认收货；
        # rejected（申请被拒）或没有售后单时正常确认收货
        latest = order.latest_aftersale
        if latest and latest.status == "pending":
            return jsonify({"code": 400,
                            "msg": "售后申请审核中，请等待审核结果"}), 400
        if latest and latest.status == "approved":
            return jsonify({"code": 400,
                            "msg": "售后已同意，无需确认收货"}), 400
        order.status = "completed"
        db.session.commit()
        msg = "已确认收货"
    elif action == "cancel":
        restore_stock(order)
        order.status = "cancelled"
        db.session.commit()
        msg = "订单已取消，库存已退回"
    else:
        return jsonify({"code": 400, "msg": "未知操作"}), 400

    return jsonify({"code": 0, "msg": msg,
                    "data": {"order_id": order.id, "status": order.status,
                             "status_text": Order.STATUS[order.status]}})


@api_bp.route("/orders/<int:oid>", methods=["GET"])
@login_required
def get_order(oid):
    """订单详情：基础信息 + 收货地址快照 + 物流信息 + 物流轨迹（track_time 正序）
    + 最新售后单（aftersale，无售后单为 null；历史 rejected 单不在此返回）。
    列表接口 order_json 已含快照/物流公司/运单号/发货时间/售后单摘要，
    详情接口额外返回 tracks 轨迹数组。"""
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    # 惰性超时关单：查看详情时若已超时，先作废再返回最新状态
    expire_order_if_needed(order)
    data = order_json(order)
    data["tracks"] = [{
        "status": t.status,
        "info": t.info,
        "track_time": t.track_time.isoformat() if t.track_time else None,
    } for t in sorted(order.tracks,
                      key=lambda t: (t.track_time or datetime.min, t.id))]
    return jsonify({"code": 0, "data": data})


# ------------ 售后（顾客端） ------------
@api_bp.route("/orders/<int:oid>/aftersale", methods=["POST"])
@login_required
def apply_aftersale(oid):
    """顾客对已发货(shipped)订单申请售后。body: {type, reason}
    - type 取值：refund_return=退货退款 / exchange=换货 / price_protect=价保
    - 订单必须属于当前用户（否则 404）；只有已发货(shipped)状态可申请：
      paid/未发货提示「订单尚未发货」，completed 提示已确认收货，cancelled 已取消
    - 防重复只看最新售后单：pending 审核中 / approved 已同意 → 400 拦截；
      rejected 已拒绝不拦截——允许重新申请，每次新建一条售后单，旧单保留为历史"""
    data = request.get_json(silent=True) or {}
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404

    aftersale_type = (data.get("type") or "").strip()
    reason = (data.get("reason") or "").strip()
    if aftersale_type not in AfterSale.TYPE_TEXT:
        return jsonify({"code": 400, "msg": "售后类型不正确，请重新选择"}), 400
    if not reason:
        return jsonify({"code": 400, "msg": "请填写申请原因"}), 400

    # 状态条件：只有 shipped（已发货、待确认收货）可以申请售后
    if order.status == "paid":
        return jsonify({"code": 400, "msg": "订单尚未发货，暂不能申请售后"}), 400
    if order.status == "completed":
        return jsonify({"code": 400, "msg": "订单已确认收货，无法申请售后"}), 400
    if order.status != "shipped":
        # pending 待支付 / cancelled 已取消等状态
        return jsonify({"code": 400,
                        "msg": f"当前订单状态[{Order.STATUS.get(order.status, order.status)}]不能申请售后"}), 400

    # 防重复：pending 审核中 / approved 已同意拦截；rejected 不拦截（可重新申请）
    latest = order.latest_aftersale
    if latest and latest.status == "pending":
        return jsonify({"code": 400,
                        "msg": "售后申请正在审核中，请勿重复提交"}), 400
    if latest and latest.status == "approved":
        return jsonify({"code": 400,
                        "msg": "该订单售后已同意，无需重复申请"}), 400

    # 智能初审：规则引擎离线给出“建议 + 可解释理由”，只落库展示，
    # 不参与任何状态流转——工单创建即 pending，必须等管理员点同意/拒绝才终裁
    ai = analyze_aftersale(aftersale_type, reason)
    record = AfterSale(order_id=order.id, user_id=g.current_user.id,
                       type=aftersale_type, reason=reason, status="pending",
                       ai_suggestion=ai["suggestion"], ai_reason=ai["reason"],
                       ai_analyzed_at=datetime.utcnow())
    db.session.add(record)
    db.session.commit()
    return jsonify({"code": 0, "msg": "售后申请已提交，请等待管理员审核",
                    "data": {"id": record.id, "status": record.status,
                             "status_text": AfterSale.STATUS_TEXT[record.status],
                             "ai_suggestion": record.ai_suggestion,
                             "ai_suggestion_text": SUGGESTION_TEXT[record.ai_suggestion],
                             "ai_reason": record.ai_reason}})


@api_bp.route("/orders/<int:oid>/aftersale", methods=["GET"])
@login_required
def get_aftersale(oid):
    """便利接口：查自己订单的最新售后单（不属于自己的订单 404；没申请过返回 null）。
    rejected 后重新申请的场景只返回最新一张，历史单在管理员售后列表中可查。"""
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    latest = order.latest_aftersale
    return jsonify({"code": 0,
                    "data": aftersale_json(latest) if latest else None})


# ------------ 商品评价（订单级，收货后评价） ------------
def _validate_rating(value):
    """严格校验评分：必须是 Python int（bool 是 int 子类，明确拒绝；
    字符串"5"、小数 2.5、None、缺失全部拒绝）且在 1-5。
    返回 (rating, err)，校验通过 err 为 None。"""
    if not isinstance(value, int) or isinstance(value, bool):
        return None, "评分必须是1-5的整数"
    if value < Review.RATING_MIN or value > Review.RATING_MAX:
        return None, "评分必须是1-5的整数"
    return value, None


@api_bp.route("/orders/<int:oid>/reviews", methods=["POST"])
@login_required
def create_order_review(oid):
    """对已完成(completed)的订单发表评价（订单级，一笔订单只能评价一次）。
    body: {rating: 1-5整数, content: 1-500字}
    - 订单不属于当前用户 → 404；订单未完成 → 400；已评价 → 400
    - rating 非整数/越界（bool/字符串/小数/None/缺失）→ 400
    - content strip 后为空 / 超 500 字 → 400
    防重复：接口先查一遍 + reviews.order_id 数据库 UNIQUE 约束双保险。"""
    data = request.get_json(silent=True) or {}
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    if order.status != "completed":
        return jsonify({"code": 400, "msg": "只有已完成的订单可以评价"}), 400
    if order.review:
        return jsonify({"code": 400, "msg": "该订单已评价，不能重复评价"}), 400

    rating, err = _validate_rating(data.get("rating"))
    if err:
        return jsonify({"code": 400, "msg": err}), 400

    content = data.get("content")
    if not isinstance(content, str):
        return jsonify({"code": 400, "msg": "评价内容不能为空"}), 400
    content = content.strip()
    if not content:
        return jsonify({"code": 400, "msg": "评价内容不能为空"}), 400
    if len(content) > Review.CONTENT_MAX_LEN:
        return jsonify({"code": 400,
                        "msg": f"评价内容最多{Review.CONTENT_MAX_LEN}字"}), 400

    review = Review(order_id=order.id, user_id=g.current_user.id,
                    rating=rating, content=content)
    db.session.add(review)
    db.session.commit()
    return jsonify({"code": 0, "msg": "评价成功", "data": review_json(review)})


@api_bp.route("/orders/<int:oid>/reviews", methods=["GET"])
@login_required
def get_order_review(oid):
    """查某订单的评价：订单属主或管理员可看，他人 → 404；无评价 data=null。"""
    order = Order.query.filter_by(id=oid).first()
    # 不暴露订单存在性：非属主且非管理员统一 404
    if not order or (order.user_id != g.current_user.id and not g.current_user.is_admin):
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    review = Review.query.filter_by(order_id=oid, is_deleted=0).first()
    return jsonify({"code": 0,
                    "data": review_json(review) if review else None})


@api_bp.route("/products/<int:pid>/reviews", methods=["GET"])
def list_product_reviews(pid):
    """商品的公开评价列表（游客可看，分页：默认 per_page=10、上限 50）。
    返回该订单内商品的全部有效评价（EXISTS 子查询去重：同订单买多件同一商品
    只出现一次），每条带脱敏买家名 + 购买商品名聚合；data.summary 为统计全部
    有效评价的汇总（与分页无关）。商品物理不存在 → 404；已下架商品的历史评价
    仍允许查看（软删除哲学：历史可追溯）。"""
    if not Product.query.get(pid):
        return jsonify({"code": 404, "msg": "商品不存在"}), 404

    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed

    q = product_review_query(pid)
    pd_data = paginate_data(q, page, per_page, review_json)
    pd_data["summary"] = product_rating_summary(pid)
    return jsonify({"code": 0, "data": pd_data})


# ------------ 支付模块（模拟微信/支付宝网关） ------------
@api_bp.route("/orders/<int:oid>/pay", methods=["POST"])
@login_required
def order_pay_create(oid):
    """统一下单：顾客选择支付方式（wechat/alipay），返回支付流水号。
    对应真实场景中"调用微信/支付宝下单接口，拿到扫码支付链接"。"""
    data = request.get_json(silent=True) or {}
    channel = (data.get("channel") or "wechat").lower()

    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    # 惰性超时关单：对已超时订单不允许再发起支付
    if expire_order_if_needed(order):
        return jsonify({"code": 400,
                        "msg": "订单超过30分钟未支付，已自动取消"}), 400

    pay_info, err = create_payment(order, channel)
    if err:
        return jsonify({"code": 400, "msg": err}), 400

    return jsonify({"code": 0, "msg": "下单成功，等待支付", "data": pay_info})


@api_bp.route("/orders/<int:oid>/pay/cancel", methods=["POST"])
@login_required
def order_pay_cancel(oid):
    """顾客主动关闭当前支付单（收银台「返回重选支付方式」时调用）。
    - 只有 pending（待支付）订单允许关闭；非 pending → 400
    - 幂等：支付单已经是关闭状态再调，返回成功不报错
    - pay_channel/trade_no 历史保留不删（支付单留痕）；重新发起支付时
      create_payment 会生成新 trade_no 并把 pay_closed 归 0
    - 关闭后旧二维码的回调在 mock-notify/handle_paid 双重拦截，订单保持 pending
    """
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404

    # 幂等：已经关闭过直接成功（不修改 pay_closed_at，保留第一次关闭的时间）
    if order.pay_closed:
        return jsonify({"code": 0, "msg": "支付单已关闭"})

    if order.status != "pending":
        return jsonify({"code": 400, "msg": "订单当前状态不能取消支付"}), 400

    order.pay_closed = 1
    order.pay_closed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"code": 0, "msg": "支付单已关闭，请重新选择支付方式",
                    "data": {"order_id": order.id, "pay_closed": 1}})


@api_bp.route("/pay/mock-notify", methods=["POST"])
def pay_mock_notify():
    """【模拟支付平台异步回调】（服务器对服务器，不要求登录 token）
    真实链路：顾客在微信/支付宝付款后，支付平台服务器主动 POST 商家的 notify_url，
             商家必须【验签】（验证通知确实来自支付平台）后才能改订单状态；
             验签通过返回 "success"，验签失败返回 "fail"，平台会重试。
    本 demo 跳过真实验签，只校验：订单存在 + 流水号对得上 + 支付单未被用户关闭
    + 状态仍为待支付。
    body: {"order_id": 订单号, "trade_no": 流水号(可选)}
    """
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")
    trade_no = data.get("trade_no")

    order = Order.query.get(order_id)
    if not order:
        # 真实支付平台约定：验签/订单异常返回 fail
        return jsonify({"code": 404, "msg": "订单不存在", "notify_result": "fail"}), 404

    # 惰性超时关单：迟到回调到达时订单已超时作废，拒绝支付（避免关单后又被置为已付）
    if expire_order_if_needed(order):
        return jsonify({"code": 400, "msg": "订单已超时取消", "notify_result": "fail"}), 400

    # 支付单已被用户主动关闭（返回重选渠道场景）：旧二维码/迟到回调必须拒绝，
    # 防止关闭旧微信单后旧回调把订单置成已付款
    if order.pay_closed:
        return jsonify({"code": 400, "msg": "支付单已关闭", "notify_result": "fail"}), 400

    # 流水号伪造：对应真实场景的"验签失败"，拒绝处理
    if trade_no and order.trade_no and trade_no != order.trade_no:
        return jsonify({"code": 400, "msg": "流水号校验失败", "notify_result": "fail"}), 400

    # 幂等：重复回调安全忽略，返回 success 让平台停止重试
    handle_paid(order, trade_no)
    return jsonify({"code": 0, "msg": "回调处理成功", "notify_result": "success"})


@api_bp.route("/orders/<int:oid>/pay-status", methods=["GET"])
@login_required
def order_pay_status(oid):
    """收银台轮询订单支付状态（前端每 1.5 秒问一次）。"""
    order = Order.query.filter_by(id=oid, user_id=g.current_user.id).first()
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    # 惰性超时关单：收银台轮询时也会把超时单作废，前端据此跳回提示重下单
    expire_order_if_needed(order)
    return jsonify({"code": 0, "data": {
        "order_id": order.id,
        "status": order.status,
        "status_text": Order.STATUS.get(order.status, order.status),
        "pay_channel": order.pay_channel,
        "trade_no": order.trade_no,
        "pay_closed": order.pay_closed or 0,  # 支付单是否已被主动关闭
        "total_amount": order.total_amount
    }})


# ------------ 后台管理（需要管理员） ------------
@api_bp.route("/admin/products", methods=["GET"])
@admin_required
def admin_list_products():
    """后台商品列表（分页 + 关键字搜索，含已下架的商品）：
    GET /api/admin/products?page=1&per_page=10&keyword=键盘
    keyword 模糊匹配商品名；在售/已下架全部返回，is_deleted 字段告诉前端当前状态。
    返回统一分页结构 data: {items, total, page, per_page, total_pages}"""
    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed
    q = Product.query
    keyword = get_keyword()
    if keyword:
        q = q.filter(Product.name.like(f"%{keyword}%"))
    q = q.order_by(Product.is_deleted.asc(), Product.id.desc())

    def admin_product_json(p):
        row = dict(product_json(p))
        row["is_deleted"] = p.is_deleted
        return row

    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, admin_product_json)})


@api_bp.route("/admin/orders", methods=["GET"])
@admin_required
def admin_list_orders():
    """全部订单（分页+状态筛选+关键字搜索）：
    GET /api/admin/orders?status=paid&page=1&per_page=10&keyword=键盘
    keyword 匹配订单号（#123 写法也支持）或订单内商品名快照，与 status AND 组合"""
    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed
    status = request.args.get("status", "")
    q = Order.query
    if status:
        q = q.filter_by(status=status)
    keyword = get_keyword()
    if keyword:
        q = q.filter(order_keyword_clause(keyword))
    q = q.order_by(Order.created_at.desc(), Order.id.desc())
    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, order_json)})


@api_bp.route("/admin/orders/<int:oid>/ship", methods=["POST"])
@admin_required
def admin_ship_order(oid):
    """发货：待发货(paid) → 已发货(shipped)
    body: {logistics_company: 物流公司（必填）, tracking_no: 运单号（必填）}
    发货同时写入物流公司/运单号/发货时间，并自动生成 4 条模拟物流轨迹
    （已揽收/运输中/派送中/已签收，发货即生成完整链路）。"""
    data = request.get_json(silent=True) or {}
    order = Order.query.get(oid)
    if not order:
        return jsonify({"code": 404, "msg": "订单不存在"}), 404
    if order.status != "paid":
        return jsonify({
            "code": 400,
            "msg": f"只有[待发货]的订单能发货，当前是[{Order.STATUS[order.status]}]"
        }), 400

    logistics_company = (data.get("logistics_company") or "").strip()
    tracking_no = (data.get("tracking_no") or "").strip()
    if not logistics_company:
        return jsonify({"code": 400, "msg": "请填写物流公司"}), 400
    if not tracking_no:
        return jsonify({"code": 400, "msg": "请填写运单号"}), 400

    shipped_at = datetime.utcnow()
    order.status = "shipped"
    order.logistics_company = logistics_company
    order.tracking_no = tracking_no
    order.shipped_at = shipped_at

    # 毕设演示用模拟轨迹（发货即生成完整链路），真实场景由快递API回调逐条写入
    mock_tracks = [
        ("已揽收", shipped_at, "快件已被揽收"),
        ("运输中", shipped_at + timedelta(hours=2), "快件已发出，正在运输途中"),
        ("派送中", shipped_at + timedelta(hours=8), "快件正在派送中，请保持电话畅通"),
        ("已签收", shipped_at + timedelta(hours=24), "快件已签收，感谢使用"),
    ]
    for status, track_time, info in mock_tracks:
        db.session.add(LogisticsTrack(
            order_id=order.id, status=status, info=info, track_time=track_time))

    db.session.commit()
    return jsonify({"code": 0, "msg": "发货成功",
                    "data": {"order_id": order.id, "status_text": "已发货",
                             "logistics_company": logistics_company,
                             "tracking_no": tracking_no}})


# ------------ 售后管理（管理员） ------------
@api_bp.route("/admin/aftersales", methods=["GET"])
@admin_required
def admin_list_aftersales():
    """售后单列表（分页 + 状态筛选 + 关键字搜索）：
    GET /api/admin/aftersales?status=pending&page=1&per_page=10&keyword=123
    keyword 匹配订单号（#123 写法也支持）/ 申请原因 / 申请人用户名，
    与 status AND 组合。按申请时间倒序；每条含售后单字段 + 订单号 + 用户名 + 订单金额。"""
    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed
    status = request.args.get("status", "")
    q = AfterSale.query
    if status:
        q = q.filter_by(status=status)
    keyword = get_keyword()
    if keyword:
        q = q.filter(aftersale_keyword_clause(keyword))
    q = q.order_by(AfterSale.created_at.desc(), AfterSale.id.desc())

    def admin_aftersale_json(a):
        row = aftersale_json(a)
        # 订单号就是 order_id（本系统订单无独立订单号字段）；
        # 用户名/金额从关联订单补，极端情况订单被硬删时兜底
        row["username"] = a.user.username if a.user else ""
        if a.order:
            row["order_amount"] = a.order.total_amount
        else:
            row["order_amount"] = None
        return row

    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, admin_aftersale_json)})


@api_bp.route("/admin/aftersales/<int:aid>/handle", methods=["POST"])
@admin_required
def admin_handle_aftersale(aid):
    """审核售后单。body: {action: "approve"|"reject", note?: 管理员备注（可选）}
    - approve 同意 → status=approved；reject 拒绝 → status=rejected
    - 只有审核中(pending)的售后单能处理，已处理返回 400
    毕设体量同意即流程终态（真实系统同意退货退款会触发退款资金流、
    换货触发补发物流，均预留扩展点）。"""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    record = AfterSale.query.get(aid)
    if not record:
        return jsonify({"code": 404, "msg": "售后单不存在"}), 404
    if record.status != "pending":
        return jsonify({"code": 400, "msg": "该售后单已处理"}), 400
    if action == "approve":
        record.status = "approved"
        msg = "已同意售后申请"
    elif action == "reject":
        record.status = "rejected"
        msg = "已拒绝售后申请"
    else:
        return jsonify({"code": 400, "msg": "操作不正确"}), 400

    # 备注可选：不传/纯空白存 None，否则去空白后存储
    note = (data.get("note") or "").strip()
    record.admin_note = note or None
    record.handled_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"code": 0, "msg": msg,
                    "data": {"id": record.id, "status": record.status,
                             "status_text": AfterSale.STATUS_TEXT[record.status]}})


# ------------ 评价管理（管理员） ------------
@api_bp.route("/admin/reviews", methods=["GET"])
@admin_required
def admin_list_reviews():
    """全部评价列表（分页 + 状态筛选 + 关键字搜索，含已删除评价）：
    GET /api/admin/reviews?status=deleted&page=1&per_page=10&keyword=键盘
    - status=all（默认，不传同）含正常+已删除；status=deleted 只看已删除
    - keyword 模糊匹配评价内容 / 买家用户名，纯数字还可按订单号精确匹配
    - 每条带订单号/用户名（后台展示真实用户名，不脱敏）/评分/内容/时间/删除状态
    按评价时间倒序。普通用户调用由 admin_required 拦截 → 403。"""
    parsed, err = get_page_params()
    if err:
        return jsonify({"code": 400, "msg": err}), 400
    page, per_page = parsed

    status = request.args.get("status", "all")
    q = Review.query
    if status == "deleted":
        q = q.filter(Review.is_deleted == 1)
    keyword = get_keyword()
    if keyword:
        q = q.filter(review_keyword_clause(keyword))
    q = q.order_by(Review.created_at.desc(), Review.id.desc())

    def admin_review_json(r):
        row = review_json(r)
        # 后台需要真实用户名做客服/追溯，覆盖公开列表的脱敏名
        row["username"] = r.user.username if r.user else ""
        row["order_no"] = r.order_id
        return row

    return jsonify({"code": 0,
                    "data": paginate_data(q, page, per_page, admin_review_json)})


@api_bp.route("/admin/reviews/<int:rid>/delete", methods=["POST"])
@admin_required
def admin_delete_review(rid):
    """软删除违规评价：is_deleted=1（公开评价列表/评分汇总立刻排除，
    后台仍可查）；已删除再次调用幂等返回成功。普通用户 → 403。"""
    review = Review.query.get(rid)
    if not review:
        return jsonify({"code": 404, "msg": "评价不存在"}), 404
    review.is_deleted = 1
    db.session.commit()
    return jsonify({"code": 0, "msg": "评价已删除"})


# ------------ 管理员数据分析看板（一期，全部 admin_required） ------------
# ---- 统一统计口径（答辩口径，改口径只改这里）----
# 1) days 白名单：只允许 7/30，默认 7；其余（0/负数/字符串/999…）一律 400。
# 2) 「有效订单」= status in (paid, shipped, completed)；pending/cancelled 不计
#    GMV/销售，但计入 order_count（下单量）。
# 3) 所有时间过滤统一用 Order.created_at（不用 paid_at），口径一致、方便测试；
#    时间窗口为 [now-days, now)，环比窗口为紧邻的上一等长周期。
# 4) 聚合优先在 Python 端做（按 created_at.date() 分桶），规避 MySQL/SQLite
#    日期函数方言差异；金额 round 2 位，百分比 round 1 位，除 0 给 None/0。
STATS_DAYS_ALLOWED = (7, 30)
STATS_VALID_STATUS = ("paid", "shipped", "completed")
STATS_TOP_LIMIT_MIN, STATS_TOP_LIMIT_MAX, STATS_TOP_LIMIT_DEFAULT = 1, 20, 5


def _parse_stats_days(default=7):
    """解析 days 查询参数：只接受 7/30（默认由调用方指定，overview/trend/briefing
    默认 7，热销榜默认 30）。返回 (days, error_response)，校验失败返回 400 响应。"""
    raw = request.args.get("days", str(default))
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "days只允许7或30"}), 400)
    # bool 是 int 子类，?days=true 这种也要挡掉
    if isinstance(raw, bool) or days not in STATS_DAYS_ALLOWED:
        return None, (jsonify({"code": 400, "msg": "days只允许7或30"}), 400)
    return days, None


def _parse_top_limit():
    """解析热销榜 limit：整数 1-20，默认 5，非法 400。"""
    raw = request.args.get("limit", str(STATS_TOP_LIMIT_DEFAULT))
    try:
        limit = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "limit必须是1-20的整数"}), 400)
    if not (STATS_TOP_LIMIT_MIN <= limit <= STATS_TOP_LIMIT_MAX):
        return None, (jsonify({"code": 400, "msg": "limit必须是1-20的整数"}), 400)
    return limit, None


def _pct_change(current, previous):
    """环比百分比（保留 1 位小数）：(current-previous)/previous*100。
    previous 为 0/None 时返回 None（不报错、不返回 inf）。"""
    if not previous:
        return None
    return round((current - previous) * 100.0 / previous, 1)


# 取数据库当前时间（不依赖 Python 进程时钟与库时区配置）
def _db_now_sql():
    return "SELECT NOW()" if db.engine.dialect.name == "mysql" \
        else "SELECT CURRENT_TIMESTAMP"


def _utc_now_sql():
    """数据库视角的 UTC 当前时间（与 Order.created_at 的 default=datetime.utcnow
    同一时区语义）。"""
    return "SELECT UTC_TIMESTAMP()" if db.engine.dialect.name == "mysql" \
        else "SELECT CURRENT_TIMESTAMP"


def _stats_base_time():
    """统计窗口右端基准（兼容 UTC / 本地时间两种 created_at 落库口径）。
    背景：Order.created_at 的 Python default 是 datetime.utcnow()，由 Python
    进程生成后经 pymysql 原样写入 DATETIME 列（不带时区转换），所以库内主流
    值是 UTC；但历史上也可能存在按本地时间(UTC+8)落库的订单。单一基准必漏
    一边：只用 utcnow() 会在凌晨漏掉本地时间订单；只用 NOW() 又会漏掉刚写入
    的 UTC 订单（NOW() 比它大 8 小时，新订单被 created_at < base 排除）。
    解法：取「DB 本地当前时间」「DB UTC 当前时间」「库内最新订单 created_at」
    三者最晚者——无论订单按哪种口径落库，刚插入的订单都一定落在窗口内。
    SQLite CURRENT_TIMESTAMP 本身就是 UTC，两路相同。"""
    def _scalar(sql):
        v = db.session.execute(text(sql)).scalar()
        if isinstance(v, str):  # SQLite 返回 "YYYY-MM-DD HH:MM:SS"
            v = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        return v

    local_now = _scalar(_db_now_sql())
    utc_now = _scalar(_utc_now_sql())
    base = local_now if local_now > utc_now else utc_now
    latest = db.session.query(db.func.max(Order.created_at)).scalar()
    if latest is not None and latest > base:
        base = latest
    return base


def _orders_in_window(start, end, statuses=None):
    """取 created_at ∈ [start, end) 的订单；statuses 非空时按状态白名单过滤。
    时间比较交给 SQLAlchemy 编译（>=、< 参数化，MySQL/SQLite 一致）。"""
    q = Order.query.filter(Order.created_at >= start, Order.created_at < end)
    if statuses:
        q = q.filter(Order.status.in_(statuses))
    return q.all()


def _overview_block(days, base):
    """计算单个等长周期的核心指标（不依赖 request，本期/环比窗口复用）。
    返回 dict：gmv/order_count/valid_order_count/avg_order_value/pay_rate/
    aftersale_rate/aftersale_count/avg_rating/rating_count。"""
    start = base - timedelta(days=days)
    all_orders = _orders_in_window(start, base)
    valid_orders = [o for o in all_orders if o.status in STATS_VALID_STATUS]

    gmv = round(sum(o.total_amount or 0 for o in valid_orders), 2)
    order_count = len(all_orders)
    valid_count = len(valid_orders)
    avg_order_value = round(gmv / valid_count, 2) if valid_count else 0
    pay_rate = round(valid_count * 100.0 / order_count, 1) if order_count else 0

    # 售后率：周期内申请的售后单数 / 有效订单数（售后单只挂在已发货/完成类
    # 有效订单上；分母为 0 时给 0，前端空态展示 0.0%）
    aftersale_count = AfterSale.query.filter(
        AfterSale.created_at >= start, AfterSale.created_at < base).count()
    aftersale_rate = round(aftersale_count * 100.0 / valid_count, 1) if valid_count else 0

    # 平均评分：周期内全部有效评价(is_deleted=0)；无评价 avg_rating=None
    rating_rows = (db.session.query(Review.rating)
                   .join(Order, Review.order_id == Order.id)
                   .filter(Review.is_deleted == 0,
                           Order.created_at >= start, Order.created_at < base).all())
    ratings = [row[0] for row in rating_rows]
    rating_count = len(ratings)
    avg_rating = round(sum(ratings) / rating_count, 1) if rating_count else None

    return {
        "gmv": gmv,
        "order_count": order_count,
        "valid_order_count": valid_count,
        "avg_order_value": avg_order_value,
        "pay_rate": pay_rate,
        "aftersale_rate": aftersale_rate,
        "aftersale_count": aftersale_count,
        "avg_rating": avg_rating,
        "rating_count": rating_count,
    }


@api_bp.route("/admin/stats/overview", methods=["GET"])
@admin_required
def admin_stats_overview():
    """核心指标卡：GMV/订单量/客单价/支付率/售后率/平均评分（默认近7天，days=7|30）。
    GMV、订单量各带 prev + change_pct 环比（其余指标不做环比）；无评价 avg_rating=null。
    GMV 只算有效订单(paid/shipped/completed)，order_count 含 pending/cancelled。"""
    days, err = _parse_stats_days()
    if err:
        return err
    base = _stats_base_time()
    cur = _overview_block(days, base)
    prev = _overview_block(days, base - timedelta(days=days))
    cur["gmv_prev"] = prev["gmv"]
    cur["gmv_change_pct"] = _pct_change(cur["gmv"], prev["gmv"])
    cur["order_count_prev"] = prev["order_count"]
    cur["order_count_change_pct"] = _pct_change(
        cur["order_count"], prev["order_count"])
    cur["days"] = days
    return jsonify({"code": 0, "data": cur})


# 统计页展示时区：订单统一按 UTC 存储，trend 按东八区归桶（存储 UTC、展示转
# 业务时区，真实项目标准做法）。只影响按天归桶，不影响金额/状态口径。
STATS_DISPLAY_TZ_OFFSET = timedelta(hours=8)


@api_bp.route("/admin/stats/sales-trend", methods=["GET"])
@admin_required
def admin_stats_sales_trend():
    """按天销售趋势：长度恰好 = days，无订单日期补零，日期升序。
    每天 {date, gmv, orders, paid_orders}；gmv/orders 只算有效订单。
    订单 created_at 按 UTC 存储，今天/物理窗口统一锚 UTC 时钟，归桶时加
    STATS_DISPLAY_TZ_OFFSET(东八区)，双库行为一致，任何时刻（含北京凌晨）
    新单都在窗内并落「今天」桶。"""
    days, err = _parse_stats_days()
    if err:
        return err
    # 展示「今天」与物理窗口统一锚定【存储时钟 UTC】：created_at 由
    # default=datetime.utcnow 经 pymysql 原样写入，库内即 UTC。绝不能用本地
    # NOW()/混时区 base 当今天，更不能把窗口右端钳到它——北京凌晨本地 NOW()
    # 已是「次日零点多」，与 UTC 末桶右端(当天16:00)跨日期比较会误判，钳制把
    # 刚下的单整段切出窗外（曾致 orders 差分恒为 0，白天跑又正常 = flaky）。
    utc_now = db.session.execute(text(_utc_now_sql())).scalar()
    if isinstance(utc_now, str):  # SQLite 返回 "YYYY-MM-DD HH:MM:SS"
        utc_now = datetime.strptime(utc_now, "%Y-%m-%d %H:%M:%S")
    today = (utc_now + STATS_DISPLAY_TZ_OFFSET).date()
    dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    buckets = {
        d: {"gmv": 0.0, "orders": 0, "paid_orders": 0} for d in dates
    }
    # 展示日桶 [dates[0] 00:00, dates[-1]+1 00:00) 对应的 UTC 物理窗口。
    # 右端固定为「北京次日零点」(=UTC 当天 16:00)，不做任何钳制：今天尚未结束，
    # 未来时刻本就没有订单；由此刚插入的 UTC 新单(物理时间∈[16:00 昨日,16:00
    # 今日))一定 < 右端而落在窗内，凌晨也不丢单。
    start = datetime.combine(dates[0], datetime.min.time()) - STATS_DISPLAY_TZ_OFFSET
    end = datetime.combine(dates[-1] + timedelta(days=1),
                           datetime.min.time()) - STATS_DISPLAY_TZ_OFFSET
    orders = _orders_in_window(start, end)
    for o in orders:
        # 按东八区展示日期归桶：凌晨刚下的单落「今天」而非昨天
        day = ((o.created_at or utc_now) + STATS_DISPLAY_TZ_OFFSET).date()
        bucket = buckets.get(day)
        if bucket is None:
            continue  # 理论上不会出现（窗口与分桶同源），防御性跳过
        # orders 记当天【全部】订单（含 pending/cancelled，反映下单量），
        # paid_orders/gmv 只记有效订单（paid/shipped/completed）
        bucket["orders"] += 1
        if o.status in STATS_VALID_STATUS:
            bucket["gmv"] += o.total_amount or 0
            bucket["paid_orders"] += 1
    return jsonify({"code": 0, "data": [
        {"date": d.isoformat(),
         "gmv": round(buckets[d]["gmv"], 2),
         "orders": buckets[d]["orders"],
         "paid_orders": buckets[d]["paid_orders"]}
        for d in dates
    ]})


@api_bp.route("/admin/stats/top-products", methods=["GET"])
@admin_required
def admin_stats_top_products():
    """热销榜：有效订单的 OrderItem 按 product_id 聚合，qty desc（并列 amount desc）。
    days=7|30（默认30），limit=1-20（默认5）；product_name 用下单时快照，
    商品之后改名/下架不影响历史榜单（与评价展示同哲学）。"""
    days, err = _parse_stats_days()
    if err:
        return err
    limit, err = _parse_top_limit()
    if err:
        return err
    base = _stats_base_time()
    start = base - timedelta(days=days)
    # 一次 JOIN 把有效订单的明细全捞出来，Python 端分组（循环内不再发查询）
    rows = (db.session.query(OrderItem.product_id, OrderItem.product_name,
                             OrderItem.price, OrderItem.quantity)
            .join(Order, OrderItem.order_id == Order.id)
            .filter(Order.status.in_(STATS_VALID_STATUS),
                    Order.created_at >= start, Order.created_at < base)
            .all())
    agg = {}
    for product_id, product_name, price, qty in rows:
        row = agg.setdefault(product_id, {"product_name": product_name,
                                         "qty": 0, "amount": 0.0})
        row["qty"] += qty or 0
        row["amount"] += (price or 0) * (qty or 0)
        # 同名快照保持首个；理论上同一商品快照名一致
    result = [
        {"product_id": pid, "product_name": v["product_name"],
         "qty": v["qty"], "amount": round(v["amount"], 2)}
        for pid, v in agg.items()
    ]
    result.sort(key=lambda r: (-r["qty"], -r["amount"], r["product_id"]))
    return jsonify({"code": 0, "data": result[:limit]})


@api_bp.route("/admin/stats/funnel", methods=["GET"])
@admin_required
def admin_stats_funnel():
    """用户转化漏斗（全量，不接 days）：按去重 user_id 计数
    users → cart_users → order_users → paid_users → completed_users。
    cart_users 取「加过购物车或下过单」的用户并集——下单链路必然经过购物车，
    用并集口径保证 5 级人数单调不增；每级 rate 为相对上一级的百分比，首级 100。"""
    users = db.session.query(User.id).count()
    cart_users = db.session.query(Cart.user_id).distinct().count()
    order_users = db.session.query(Order.user_id).distinct().count()
    paid_users = (db.session.query(Order.user_id).distinct()
                  .filter(Order.status.in_(STATS_VALID_STATUS)).count())
    completed_users = (db.session.query(Order.user_id).distinct()
                       .filter(Order.status == "completed").count())
    # 口径兜底：下过单但购物车已被清空的用户也算触达过购物车，保证单调
    cart_users = max(cart_users, order_users)
    steps = [
        ("users", "注册用户", users),
        ("cart_users", "加购用户", cart_users),
        ("order_users", "下单用户", order_users),
        ("paid_users", "有效支付用户", paid_users),
        ("completed_users", "完成订单用户", completed_users),
    ]
    data = []
    prev_count = None
    for key, label, count in steps:
        if prev_count is None:
            rate = 100.0
        else:
            rate = round(count * 100.0 / prev_count, 1) if prev_count else 0
        data.append({"key": key, "label": label, "users": count, "rate": rate})
        prev_count = count
    return jsonify({"code": 0, "data": data})


def _ai_aftersale_stats():
    """AI 售后初审效果统计（全量）：分析覆盖、建议分布、人工一致率。
    一致性只对 status in (approved,rejected) 且 ai_suggestion in
    (approve,reject) 的工单计算——manual 本就交人工，不进分母。"""
    all_rows = AfterSale.query.all()
    total = len(all_rows)
    analyzed = 0
    pending = 0
    dist = {"approve": 0, "reject": 0, "manual": 0}
    handled_total = 0
    agreed = 0
    decided_total = 0
    for a in all_rows:
        sug = a.ai_suggestion
        if sug in dist:
            # 含未处理工单的建议分布（建议在申请时就已生成）
            dist[sug] += 1
            analyzed += 1
        if a.status == "pending":
            pending += 1
        if a.status in ("approved", "rejected"):
            handled_total += 1
            if sug in ("approve", "reject"):
                decided_total += 1
                if ((sug == "approve" and a.status == "approved") or
                        (sug == "reject" and a.status == "rejected")):
                    agreed += 1
    agreement_rate = round(agreed * 100.0 / decided_total, 1) if decided_total else None
    return {
        "total": total,
        "analyzed": analyzed,
        "pending": pending,
        "suggestion_dist": dist,
        "handled_total": handled_total,
        "agreed": agreed,
        "decided_total": decided_total,
        "agreement_rate": agreement_rate,
    }


@api_bp.route("/admin/stats/ai/aftersale", methods=["GET"])
@admin_required
def admin_stats_ai_aftersale():
    """AI 初审效果面板（全量）：总量/已分析/待处理/建议分布/已处理/一致数/采纳率。
    老工单 ai_suggestion 为 NULL 时 analyzed 不计数、结构恒定不报错。"""
    return jsonify({"code": 0, "data": _ai_aftersale_stats()})


@api_bp.route("/admin/stats/ai/briefing", methods=["GET"])
@admin_required
def admin_stats_ai_briefing():
    """AI 经营简报（默认近7天，days=7|30）：routes 层只负责取数组装 stats dict，
    文案生成交给 app/dashboard_brief.py 的纯函数 generate_briefing（不碰 DB/网络，
    模板式 NLG，未来可整体替换为大模型调用而不改本接口形状）。"""
    days, err = _parse_stats_days()
    if err:
        return err
    base = _stats_base_time()
    cur = _overview_block(days, base)
    prev = _overview_block(days, base - timedelta(days=days))
    cur["gmv_prev"] = prev["gmv"]
    cur["gmv_change_pct"] = _pct_change(cur["gmv"], prev["gmv"])
    cur["order_count_prev"] = prev["order_count"]
    cur["order_count_change_pct"] = _pct_change(
        cur["order_count"], prev["order_count"])
    cur["days"] = days

    start = base - timedelta(days=days)
    rows = (db.session.query(OrderItem.product_id, OrderItem.product_name,
                             OrderItem.price, OrderItem.quantity)
            .join(Order, OrderItem.order_id == Order.id)
            .filter(Order.status.in_(STATS_VALID_STATUS),
                    Order.created_at >= start, Order.created_at < base)
            .all())
    top_agg = {}
    for product_id, product_name, price, qty in rows:
        row = top_agg.setdefault(product_id, {"product_name": product_name,
                                              "qty": 0, "amount": 0.0})
        row["qty"] += qty or 0
        row["amount"] += (price or 0) * (qty or 0)
    top_products = [
        {"product_id": pid, "product_name": v["product_name"],
         "qty": v["qty"], "amount": round(v["amount"], 2)}
        for pid, v in top_agg.items()]
    top_products.sort(key=lambda r: (-r["qty"], -r["amount"], r["product_id"]))

    stats = {
        "days": days,
        "overview": cur,
        "trend": [],
        "top_products": top_products[:STATS_TOP_LIMIT_DEFAULT],
        "ai_panel": _ai_aftersale_stats(),
    }
    return jsonify({"code": 0, "data": generate_briefing(stats)})


# ------------ 管理员数据分析看板（二期：深度洞察，全部 admin_required） ------------
# 与一期口径完全一致：Python 端聚合、时间比较走 SQLAlchemy 参数化、
# created_at 时间窗口、有效订单 STATS_VALID_STATUS。
RFM_DAYS_ALLOWED = (7, 30, 90)
INV_THRESHOLD_MIN, INV_THRESHOLD_MAX, INV_THRESHOLD_DEFAULT = 1, 100, 10
# 差评预警 / 内容片段长度上限（防超长文本把接口撑爆）
REVIEW_ALERT_LIMIT = 50
REVIEW_SNIPPET_LEN = 60


def _parse_rfm_days(default=30):
    """解析 RFM 的 days：白名单 7/30/90（RFM 分析窗口比一期放宽到 90），
    默认 30；非法（0/负数/字符串/bool/999…）→ 400。思路与一期
    _parse_stats_days 一致，仅白名单与提示语不同。"""
    raw = request.args.get("days", str(default))
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "days只允许7、30或90"}), 400)
    if isinstance(raw, bool) or days not in RFM_DAYS_ALLOWED:
        return None, (jsonify({"code": 400, "msg": "days只允许7、30或90"}), 400)
    return days, None


def _parse_inventory_threshold():
    """严格解析库存预警 threshold 查询参数。
    规则（参考 _validate_rating 的严格类型口径）：
    - 必须能严格表示为 int：bool、字符串、小数（1.5/100.0）、None/缺失以外的
      非法值全部 400；缺失时用默认 10；
    - 整数范围 1-100（0/101/-1 → 400）。
    返回 (threshold, error_response)。
    """
    raw = request.args.get("threshold", str(INV_THRESHOLD_DEFAULT))
    # 字符串形式 "10" 允许（查询参数本来就是字符串），但 "1.5"/"10.0"/"abc"/"" 拒绝
    text = str(raw).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        return None, (jsonify({"code": 400,
                               "msg": "threshold必须是1-100的整数"}), 400)
    threshold = int(text)
    if not (INV_THRESHOLD_MIN <= threshold <= INV_THRESHOLD_MAX):
        return None, (jsonify({"code": 400,
                               "msg": "threshold必须是1-100的整数"}), 400)
    return threshold, None


@api_bp.route("/admin/stats/review-insights", methods=["GET"])
@admin_required
def admin_stats_review_insights():
    """评价标签洞察（默认近7天，days=7|30，口径同一期）：
    - 窗口内 is_deleted=0 的 Review join Order，按 Order.created_at 过滤；
    - 调 app/review_ai.py 的纯函数做标签提取与聚合；
    - 差评预警：rating<=2 的评价（id/脱敏用户名/评分/内容片段/时间）；
    返回正/负标签 TOP（含 count）、差评预警列表、summary 计数。"""
    days, err = _parse_stats_days()
    if err:
        return err
    base = _stats_base_time()
    start = base - timedelta(days=days)

    # 一次 JOIN 把窗口内有效评价全捞出（Python 端聚合，循环内不再发查询）
    rows = (db.session.query(Review, User)
            .join(Order, Review.order_id == Order.id)
            .outerjoin(User, Review.user_id == User.id)
            .filter(Review.is_deleted == 0,
                    Order.created_at >= start, Order.created_at < base)
            .order_by(Review.created_at.desc(), Review.id.desc())
            .all())

    entries = []
    alerts = []
    negative_count = 0
    rating_sum = 0
    for review, user in rows:
        entries.append({"content": review.content, "rating": review.rating})
        rating_sum += review.rating or 0
        if review.rating is not None and review.rating <= 2:
            negative_count += 1
            if len(alerts) < REVIEW_ALERT_LIMIT:
                content = review.content or ""
                snippet = content if len(content) <= REVIEW_SNIPPET_LEN \
                    else content[:REVIEW_SNIPPET_LEN] + "..."
                alerts.append({
                    "id": review.id,
                    "username": mask_username(user.username if user else ""),
                    "rating": review.rating,
                    "snippet": snippet,
                    "order_id": review.order_id,
                    "created_at": review.created_at.isoformat()
                    if review.created_at else None,
                })

    tags = aggregate_review_tags(entries)
    total = tags["total"]
    data = {
        "days": days,
        "positive_tags": tags["positive"],
        "negative_tags": tags["negative"],
        "tagged_rate": tags["tagged_rate"],
        "sentiment_dist": tags["sentiment_dist"],
        "bad_reviews": alerts,
        "summary": {
            "total_reviews": total,
            "tagged_reviews": tags["tagged"],
            "bad_review_count": negative_count,
            "avg_rating": round(rating_sum / total, 1) if total else None,
        },
    }
    return jsonify({"code": 0, "data": data})


@api_bp.route("/admin/stats/inventory-alerts", methods=["GET"])
@admin_required
def admin_stats_inventory_alerts():
    """库存预警（默认 threshold=10）：
    - threshold 严格校验（int，1-100；bool/字符串/小数/越界 → 400）；
    - 只查 is_deleted=0（在售）商品中 stock <= threshold 的，已下架商品排除；
    - stock 升序、id 升序；stock=0 → out_of_stock，1<=stock<=threshold → low。
    返回 {items, total, threshold}。"""
    threshold, err = _parse_inventory_threshold()
    if err:
        return err
    products = (Product.query
                .filter(Product.is_deleted == 0, Product.stock <= threshold)
                .order_by(Product.stock.asc(), Product.id.asc())
                .all())
    items = [{
        "id": p.id,
        "name": p.name,
        "stock": p.stock,
        "price": p.price,
        "status": "out_of_stock" if p.stock == 0 else "low",
    } for p in products]
    return jsonify({"code": 0, "data": {
        "items": items, "total": len(items), "threshold": threshold}})


@api_bp.route("/admin/stats/rfm", methods=["GET"])
@admin_required
def admin_stats_rfm():
    """用户 RFM 分层（days=7|30|90，默认30）：
    - 只基于窗口内有效订单(STATS_VALID_STATUS)，按 user_id 聚合：
      R=基准日-最近一笔有效订单 created_at 的天数（向下取整整天）、
      F=有效订单数、M=total_amount 合计；
    - 打分分层交给 app/rfm_analysis.py 的纯函数（全体中位数切点，8 类经典分层）；
    - 无有效订单用户不进 RFM；空窗口结构完整，无 NaN/inf。"""
    days, err = _parse_rfm_days()
    if err:
        return err
    base = _stats_base_time()
    start = base - timedelta(days=days)
    orders = _orders_in_window(start, base, STATS_VALID_STATUS)

    agg = {}
    for o in orders:
        row = agg.setdefault(o.user_id, {"last": None, "f": 0, "m": 0.0,
                                         "username": ""})
        created = o.created_at or base
        if row["last"] is None or created > row["last"]:
            row["last"] = created
        row["f"] += 1
        row["m"] += o.total_amount or 0

    users = []
    for uid, row in agg.items():
        last = row["last"] or base
        delta = base - last
        r_days = delta.days if delta.days >= 0 else 0
        user = User.query.get(uid)
        users.append({
            "user_id": uid,
            "username": user.username if user else "",
            "r_days": r_days,
            "f": row["f"],
            "m": round(row["m"], 2),
        })

    result = build_rfm(users, days=days)
    return jsonify({"code": 0, "data": result})


@api_bp.route("/admin/products", methods=["POST"])
@admin_required
def admin_create_product():
    """新增商品 body: {name, price, stock, description, image(可选)}
    image 只填文件名，如 my.png，图片文件放在 app/static/images/ 目录"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    price = data.get("price")
    image = (data.get("image") or "").strip()

    if not name:
        return jsonify({"code": 400, "msg": "商品名不能为空"}), 400
    try:
        price = float(price)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "价格必须是数字"}), 400
    if price <= 0:
        return jsonify({"code": 400, "msg": "价格必须大于0"}), 400
    stock, stock_err = parse_int_field(data.get("stock", 0))
    if stock_err:
        return jsonify({"code": 400, "msg": "库存" + stock_err}), 400
    if stock < 0:
        return jsonify({"code": 400, "msg": "库存不能为负"}), 400

    p = Product(name=name, price=price, stock=stock,
                description=(data.get("description") or "").strip(), image=image)
    db.session.add(p)
    db.session.commit()
    return jsonify({"code": 0, "msg": "商品已添加", "data": {"id": p.id}})


@api_bp.route("/admin/products/<int:pid>", methods=["PUT"])
@admin_required
def admin_update_product(pid):
    """修改商品（改名字/价格/库存/描述/图片，传哪个改哪个）"""
    p = Product.query.get(pid)
    if not p:
        return jsonify({"code": 404, "msg": "商品不存在"}), 404
    if p.is_deleted:
        return jsonify({"code": 400, "msg": "商品已下架，请先恢复再修改"}), 400

    data = request.get_json(silent=True) or {}
    if "name" in data and str(data["name"]).strip():
        p.name = str(data["name"]).strip()
    if "price" in data:
        try:
            price = float(data["price"])
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "价格必须是数字"}), 400
        if price <= 0:
            return jsonify({"code": 400, "msg": "价格必须大于0"}), 400
        p.price = price
    if "stock" in data:
        stock, stock_err = parse_int_field(data["stock"])
        if stock_err:
            return jsonify({"code": 400, "msg": "库存" + stock_err}), 400
        if stock < 0:
            return jsonify({"code": 400, "msg": "库存不能为负"}), 400
        p.stock = stock
    if "description" in data:
        p.description = str(data.get("description") or "")
    # 图片：传了 image 字段就更新（空字符串也生效，表示清空走兜底）
    if "image" in data:
        p.image = str(data.get("image") or "").strip()

    db.session.commit()
    return jsonify({"code": 0, "msg": "商品已更新"})


@api_bp.route("/admin/products/<int:pid>", methods=["DELETE"])
@admin_required
def admin_delete_product(pid):
    """软删除商品：标记 is_deleted=1（顾客端立刻不可见、不能购买），
    数据行保留——历史订单/支付流水永远可追溯；购物车里的这件商品一并清掉。
    删错了可以用 POST /admin/products/<id>/restore 恢复上架。"""
    p = Product.query.get(pid)
    if not p:
        return jsonify({"code": 404, "msg": "商品不存在"}), 404
    if p.is_deleted:
        return jsonify({"code": 400, "msg": "商品已下架，无需重复操作"}), 400

    p.is_deleted = 1
    Cart.query.filter_by(product_id=pid).delete()
    db.session.commit()
    return jsonify({"code": 0, "msg": "商品已下架（可在商品管理中恢复）"})


@api_bp.route("/admin/products/<int:pid>/restore", methods=["POST"])
@admin_required
def admin_restore_product(pid):
    """恢复已下架商品：is_deleted 改回 0，重新对顾客可见可买"""
    p = Product.query.get(pid)
    if not p:
        return jsonify({"code": 404, "msg": "商品不存在"}), 404
    if not p.is_deleted:
        return jsonify({"code": 400, "msg": "商品在售中，无需恢复"}), 400

    p.is_deleted = 0
    db.session.commit()
    return jsonify({"code": 0, "msg": "商品已恢复上架"})

# ------------ AI 模块（智能客服 + 智能推荐，网关适配，见 app/ai_service.py） ------------
@api_bp.route("/ai/chat", methods=["POST"])
def ai_chat():
    """智能客服对话接口（游客可用；涉及查订单等个人数据时后端做登录/越权校验）。
    body: {"message": "用户说的话"}
    返回: {"reply": 回复文本, "intent": 意图标签, "products": 商品卡片列表, "need_login": bool}
    """
    from app.ai_service import chat as ai_chat_service

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"code": 400, "msg": "消息不能为空"}), 400
    if len(message) > 500:
        return jsonify({"code": 400, "msg": "消息太长了"}), 400

    result = ai_chat_service(optional_user(), message)
    return jsonify({"code": 0, "data": {
        "reply": result["reply"],
        "intent": result["intent"],
        "need_login": result.get("need_login", False),
        "products": [product_json(p) for p in result.get("products", [])],
    }})


@api_bp.route("/ai/recommend", methods=["GET"])
def ai_recommend():
    """「猜你喜欢」推荐接口（游客可用，登录后按购物车/历史订单做个性化推荐）。"""
    from app.ai_service import recommend_products

    products = recommend_products(optional_user(), limit=4)
    return jsonify({"code": 0, "data": [product_json(p) for p in products]})
