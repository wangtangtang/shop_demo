// ========== 通用工具 ==========
const TOKEN_KEY = "shop_token";
const USER_KEY = "shop_user";
const ADMIN_KEY = "shop_is_admin";

function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

function setAuth(token, username, isAdmin) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, username);
    localStorage.setItem(ADMIN_KEY, isAdmin ? "1" : "0");
}

function clearAuth() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ADMIN_KEY);
}

function getUsername() {
    return localStorage.getItem(USER_KEY);
}

// HTML 转义：渲染用户输入的文本时用，防止引号/尖括号破坏页面结构
function escapeHtml(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function isAdmin() {
    return localStorage.getItem(ADMIN_KEY) === "1";
}

function authHeaders() {
    const token = getToken();
    return token ? { "Authorization": "Bearer " + token } : {};
}

// 统一封装 fetch
async function api(url, options = {}) {
    const opts = {
        headers: { "Content-Type": "application/json", ...authHeaders() },
        ...options
    };
    if (opts.body && typeof opts.body !== "string") {
        opts.body = JSON.stringify(opts.body);
    }
    const resp = await fetch(url, opts);
    const data = await resp.json().catch(() => ({}));
    return { status: resp.status, data };
}

// Toast 提示
function toast(msg, type = "") {
    const old = document.querySelector(".toast");
    if (old) old.remove();
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2200);
}

// 更新导航栏用户状态（管理员额外显示「后台管理」入口）
function renderUserArea() {
    const area = document.getElementById("user-area");
    if (!area) return;
    const name = getUsername();
    if (name) {
        const adminLink = isAdmin()
            ? `<a href="/admin" style="color:#0984e3;font-weight:700">🛠️ 后台管理</a>`
            : "";
        area.innerHTML = `
            <div class="user-info">
                <a href="/orders" style="color:#636e72">我的订单</a>
                <a href="/addresses" style="color:#636e72">收货地址</a>
                ${adminLink}
                <span>👤 ${name}</span>
                <button class="btn-logout" onclick="logout()">退出</button>
            </div>
        `;
    } else {
        area.innerHTML = `<button class="btn-login" onclick="location.href='/login'">登录</button>`;
    }
}

function logout() {
    clearAuth();
    toast("已退出登录", "success");
    setTimeout(() => location.href = "/", 600);
}

async function updateCartBadge() {
    const badge = document.getElementById("cart-badge");
    if (!badge || !getToken()) {
        if (badge) badge.style.display = "none";
        return;
    }
    try {
        const { data } = await api("/api/cart");
        if (data.code === 0 && data.data.items.length > 0) {
            const count = data.data.items.reduce((s, i) => s + i.quantity, 0);
            badge.textContent = count;
            badge.style.display = "flex";
        } else {
            badge.style.display = "none";
        }
    } catch (e) {
        badge.style.display = "none";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    renderUserArea();
    updateCartBadge();
});

// ========== 通用分页栏 ==========
// container: 分页栏容器元素；pager: {total, page, per_page, total_pages}
// onGo(page, per_page): 翻页 / 切换每页条数时的回调
// sizeOptions: 每页条数可选值，默认 [5, 10, 20]（5 方便毕设演示分页）
function renderPager(container, pager, onGo, sizeOptions = [5, 10, 20]) {
    container.innerHTML = "";
    container.style.display = pager.total > 0 ? "flex" : "none";
    if (!pager.total) return;

    const info = document.createElement("span");
    info.className = "pager-info";
    info.textContent = `共 ${pager.total} 条 / ${pager.total_pages} 页`;
    container.appendChild(info);

    const sizes = document.createElement("span");
    sizes.className = "pager-sizes";
    sizes.textContent = "每页 ";
    sizeOptions.forEach(n => {
        const b = document.createElement("button");
        b.textContent = n;
        b.className = "pager-size-btn" + (n === pager.per_page ? " active" : "");
        b.onclick = () => onGo(1, n);  // 切每页条数后回到第 1 页
        sizes.appendChild(b);
    });
    sizes.appendChild(document.createTextNode(" 条"));
    container.appendChild(sizes);

    const pages = document.createElement("span");
    pages.className = "pager-pages";

    const mkBtn = (text, pg, opts = {}) => {
        const b = document.createElement("button");
        b.textContent = text;
        b.className = "pager-btn" + (opts.cur ? " active" : "");
        if (opts.disabled) { b.disabled = true; b.className += " disabled"; }
        if (!opts.disabled && !opts.cur) b.onclick = () => onGo(pg, pager.per_page);
        return b;
    };

    pages.appendChild(mkBtn("‹ 上一页", pager.page - 1, { disabled: pager.page <= 1 }));

    // 页码窗口：当前页左右各 2 页，用 ... 折叠
    const set = new Set([1, pager.total_pages,
        ...[pager.page - 2, pager.page - 1, pager.page, pager.page + 1, pager.page + 2]
        .filter(n => n >= 1 && n <= pager.total_pages)]);
    const nums = [...set].sort((a, b) => a - b);
    let prev = 0;
    nums.forEach(n => {
        if (prev && n - prev > 1) {
            const dot = document.createElement("span");
            dot.textContent = "…";
            dot.className = "pager-dots";
            pages.appendChild(dot);
        }
        pages.appendChild(mkBtn(String(n), n, { cur: n === pager.page }));
        prev = n;
    });

    pages.appendChild(mkBtn("下一页 ›", pager.page + 1,
        { disabled: pager.page >= pager.total_pages }));
    container.appendChild(pages);
}
