"""页面路由（非 API），返回 HTML 模板"""
from flask import Blueprint, render_template, session, redirect, url_for

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("index.html")


@pages_bp.route("/login")
def login():
    return render_template("login.html")


@pages_bp.route("/register")
def register_page():
    """注册页（与登录页分开）"""
    return render_template("register.html")


@pages_bp.route("/cart")
def cart():
    return render_template("cart.html")


@pages_bp.route("/orders")
def orders():
    return render_template("orders.html")


@pages_bp.route("/addresses")
def addresses():
    """收货地址簿页（是否登录由前端调接口时拦截）"""
    return render_template("addresses.html")


@pages_bp.route("/product/<int:pid>")
def product_detail(pid):
    """商品详情页：多图轮播 + 描述 + 加购（是否存在/下架由前端调接口校验）"""
    return render_template("product.html", product_id=pid)


@pages_bp.route("/pay/<int:oid>")
def checkout(oid):
    """收银台页：选支付方式 → 扫码 → 模拟支付"""
    return render_template("checkout.html", order_id=oid)


@pages_bp.route("/admin")
def admin():
    """后台管理页：服务端先校验登录态与管理员角色，再返回页面。
    - 未登录 → 跳转登录页（登录后再进后台）
    - 已登录但非管理员 → 403
    - 管理员 → 返回后台页（具体数据仍由各 /api/admin 接口二次鉴权）"""
    if not session.get("user_id"):
        return redirect(url_for("pages.login"))
    if not session.get("is_admin"):
        return "403 Forbidden：需要管理员权限", 403
    return render_template("admin.html")
