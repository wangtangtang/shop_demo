from datetime import datetime
from app import db
from app.security import hash_password, verify_password, is_hashed


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    # 存储 PBKDF2 哈希（非明文）；长度放宽以容纳哈希串
    password = db.Column(db.String(255), nullable=False)
    # is_admin=1 是管理员（后台账号），0 是普通顾客
    is_admin = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        """设置密码：写入加盐哈希，绝不存明文。"""
        self.password = hash_password(raw)

    def check_password(self, raw):
        """校验密码。迁移过渡期兼容老明文：明文密码直接相等比较，
        登录成功后由调用方自动升级为哈希。"""
        if is_hashed(self.password):
            return verify_password(raw, self.password)
        # 老库尚未迁移的明文记录：常量时间比较，避免计时差异
        import hmac
        return hmac.compare_digest(str(self.password), str(raw))

    carts = db.relationship("Cart", backref="user", lazy=True)
    orders = db.relationship("Order", backref="user", lazy=True)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    description = db.Column(db.Text, default="")
    # 商品图片文件名（图片放在 app/static/images/ 目录）
    # 图片与商品 id 解耦：清库重置、id 顺延都不影响图片显示
    image = db.Column(db.String(200), default="")
    # 软删除标记：0=在售，1=已下架(删除)。下架后顾客端不可见，但数据保留，
    # 历史订单/流水仍可追溯；管理员可在后台「恢复」重新上架
    is_deleted = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Cart(db.Model):
    __tablename__ = "carts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1)

    product = db.relationship("Product")


class Address(db.Model):
    """收货地址簿：一个用户可以有多条地址，其中一条是默认地址（is_default=1）。
    下单时把地址内容拷贝成快照存进订单，地址之后改/删不影响历史订单。"""
    __tablename__ = "addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    receiver_name = db.Column(db.String(50), nullable=False)   # 收货人
    receiver_phone = db.Column(db.String(20), nullable=False)  # 手机号
    region = db.Column(db.String(200), nullable=False)         # 省市区
    detail = db.Column(db.Text, nullable=False)                # 详细地址
    # 默认地址标记：0=普通，1=默认。每个用户最多一条默认地址（由接口保证）
    is_default = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Order(db.Model):
    __tablename__ = "orders"

    # ---- 订单状态字典（整个系统共用这一份，改状态只改这里）----
    # pending 待支付 → paid 待发货(已付款) → shipped 已发货 → completed 已完成
    #                 任意环节可 → cancelled 已取消
    STATUS = {
        "pending": "待支付",
        "paid": "待发货",
        "shipped": "已发货",
        "completed": "已完成",
        "cancelled": "已取消",
    }

    # 每个状态允许执行的操作：pay付款 / ship发货 / confirm收货 / cancel取消
    ACTIONS = {
        "pending":   ["pay", "cancel"],
        "paid":      ["ship", "cancel"],
        "shipped":   ["confirm"],
        "completed": [],
        "cancelled": [],
    }

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    # 下单后默认 pending(待支付)
    status = db.Column(db.String(20), default="pending")
    # ---- 支付相关（模拟支付网关）----
    pay_channel = db.Column(db.String(20), default="")   # 支付渠道：wechat/alipay
    trade_no = db.Column(db.String(64), default="")      # 支付平台交易流水号
    paid_at = db.Column(db.DateTime, nullable=True)      # 支付完成时间
    # ---- 支付单关闭（顾客在收银台「返回重选支付方式」时主动关闭当前支付单）----
    # pay_closed=1 表示当前支付单已作废：旧二维码/迟到回调一律拒绝，防止用户关闭
    # 微信单后旧二维码的回调把订单置成已付款；pay_channel/trade_no 历史保留不删
    # （支付单留痕），重新发起支付时生成新 trade_no 并把 pay_closed 重置为 0。
    pay_closed = db.Column(db.Integer, default=0)        # 0=支付单有效，1=已关闭
    pay_closed_at = db.Column(db.DateTime, nullable=True)  # 支付单关闭时间
    # ---- 收货 & 物流相关 ----
    # 下单时的收货地址快照（JSON 字符串：receiver_name/receiver_phone/region/detail）。
    # 与 OrderItem 存商品名/价格快照同一设计哲学：历史订单永远保留下单时的样子
    address_snapshot = db.Column(db.Text, nullable=True)
    logistics_company = db.Column(db.String(50), nullable=True)  # 物流公司
    tracking_no = db.Column(db.String(64), nullable=True)        # 运单号
    shipped_at = db.Column(db.DateTime, nullable=True)           # 发货时间
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship("OrderItem", backref="order", lazy=True,
                            cascade="all, delete-orphan")
    tracks = db.relationship("LogisticsTrack", backref="order", lazy=True,
                             cascade="all, delete-orphan")
    # 售后单：一个订单可以有多张（申请被拒绝后允许重新申请，旧单保留为历史），
    # 删订单时级联删除；aftersales 按申请时间正序排列，最新的一张取 latest_aftersale
    aftersales = db.relationship("AfterSale", backref="order", lazy=True,
                                 cascade="all, delete-orphan",
                                 order_by=lambda: (AfterSale.created_at.asc(),
                                                   AfterSale.id.asc()))
    # 商品评价：订单级一对一（一笔订单只能评价一次，DB 层 unique 约束兜底），
    # 删订单时级联删除
    review = db.relationship("Review", uselist=False, backref="order",
                             cascade="all, delete-orphan")

    @property
    def latest_aftersale(self):
        """最新一张售后单（没有则 None）。按 created_at 倒序、id 倒序兜底，
        同一秒内连续申请（如 rejected 后立即重新申请）也能取到最新一张。"""
        if not self.aftersales:
            return None
        return sorted(self.aftersales,
                      key=lambda a: (a.created_at or datetime.min, a.id),
                      reverse=True)[0]


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)


class LogisticsTrack(db.Model):
    """物流轨迹：发货时一次性生成完整链路「已揽收 → 运输中 → 派送中 → 已签收」
    共 4 条模拟轨迹（毕设演示用），真实场景由快递 API 回调逐条写入。"""
    __tablename__ = "logistics_tracks"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    status = db.Column(db.String(50), nullable=False)    # 轨迹状态文字，如「已揽收」
    info = db.Column(db.Text, default="")                # 轨迹描述
    track_time = db.Column(db.DateTime, default=datetime.utcnow)  # 轨迹发生时间
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AfterSale(db.Model):
    """售后单：已发货(shipped)订单可由顾客申请售后，管理员在后台审核。
    类型：退货退款 / 换货 / 价保；状态：审核中(pending) → 已同意(approved)/已拒绝(rejected)。
    一个订单可以有多张售后单（Order.aftersales 一对多）：申请被拒绝(rejected)后
    允许重新申请，旧单保留为历史；pending/approved 状态下接口层拦截重复申请。"""
    __tablename__ = "after_sales"

    # ---- 售后类型 / 状态字典（展示层共用这一份，改文案只改这里）----
    TYPE_TEXT = {
        "refund_return": "退货退款",
        "exchange": "换货",
        "price_protect": "价保",
    }
    STATUS_TEXT = {
        "pending": "审核中",
        "approved": "已同意",
        "rejected": "已拒绝",
    }

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)               # refund_return/exchange/price_protect
    reason = db.Column(db.Text, default="")                       # 顾客填写的申请原因
    status = db.Column(db.String(20), default="pending")          # pending/approved/rejected
    admin_note = db.Column(db.String(255), nullable=True)         # 管理员审核备注（可空）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # 申请时间
    handled_at = db.Column(db.DateTime, nullable=True)            # 审核处理时间
    # ---- 售后智能初审（规则引擎建议，仅“建议”，不改变任何状态；终裁仍以管理员 handle 为准）----
    ai_suggestion = db.Column(db.String(20), nullable=True)       # approve/reject/manual
    ai_reason = db.Column(db.Text, nullable=True)                 # 可解释理由（给管理员看）
    ai_analyzed_at = db.Column(db.DateTime, nullable=True)        # 初审分析时间

    user = db.relationship("User")


class Review(db.Model):
    """商品评价（订单级）：订单确认收货(completed)后由顾客对整笔订单评价一次。
    一笔订单一条评价（order_id 唯一 + 接口双校验），展示时把该订单所有
    OrderItem 的商品名聚合为「购买商品」快照；管理员可软删除违规评价
    （is_deleted=1，公开评价列表/汇总自动排除，后台仍可见，与商品软删除同一哲学）。"""
    __tablename__ = "reviews"

    # 评分边界（整个系统共用这一份，接口校验/前端展示都引用）
    RATING_MIN = 1
    RATING_MAX = 5
    # 评价内容最大长度（按 Python 字符数 len 校验，非字节数）
    CONTENT_MAX_LEN = 500

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"),
                         unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)             # 评分 1-5
    content = db.Column(db.Text, nullable=False)               # 评价内容（strip 后非空，<=500 字）
    is_deleted = db.Column(db.Integer, default=0)              # 0=正常，1=管理员已删除
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
