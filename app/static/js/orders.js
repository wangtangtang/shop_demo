// ========== 订单列表（分页 + 关键字搜索） ==========
let orderPage = 1;    // 当前页
let orderSize = 10;   // 每页条数（可在页面上切 5/10/20，切 5 方便演示分页）
let orderKeyword = "";  // 当前搜索关键字（空串 = 不搜索；点搜索/回车时才更新）

const STATUS_MAP = {
    pending:   { text: "待支付", color: "#ffa502" },
    paid:      { text: "待发货", color: "#00b894" },
    shipped:   { text: "已发货", color: "#6c5ce7" },
    completed: { text: "已完成", color: "#0984e3" },
    cancelled: { text: "已取消", color: "#b2bec3" }
};

// 售后状态展示（黄=审核中 / 绿=已同意 / 红=已拒绝）
const AS_STATUS_MAP = {
    pending:  { text: "售后审核中", cls: "as-pending" },
    approved: { text: "售后已同意", cls: "as-approved" },
    rejected: { text: "售后已拒绝", cls: "as-rejected" }
};
const AS_TYPE_TEXT = {
    refund_return: "退货退款",
    exchange: "换货",
    price_protect: "价保"
};

async function goOrder(page, size) {
    orderPage = page;
    if (size) orderSize = size;
    await loadOrders();
}

// 搜索我的订单：读输入框（去空格）→ 更新关键字并回到第 1 页；清空输入即恢复全部订单
async function searchMyOrders() {
    orderKeyword = document.getElementById("order-keyword").value.trim();
    orderPage = 1;
    await loadOrders();
}

// 空态里的「清空搜索条件」：清空输入框 + 关键字，回到第 1 页
async function clearOrderSearch() {
    document.getElementById("order-keyword").value = "";
    orderKeyword = "";
    orderPage = 1;
    await loadOrders();
}

// 订单操作按钮：pay=跳收银台（走支付网关）；confirm=确认收货；cancel=取消订单（退库存）
async function orderAct(oid, action) {
    if (action === "pay") {
        location.href = `/pay/${oid}`;
        return;
    }
    if (action === "cancel" && !confirm("确定取消这笔订单吗？库存会自动退回")) return;

    const { data } = await api(`/api/orders/${oid}/action`, {
        method: "POST",
        body: { action }
    });
    if (data.code === 0) {
        toast(data.msg || "操作成功", "success");
        await loadOrders();
    } else {
        toast(data.msg || "操作失败", "error");
    }
}

async function loadOrders() {
    if (!getToken()) {
        location.href = "/login";
        return;
    }

    const list = document.getElementById("order-list");
    const pagerBox = document.getElementById("order-pager");
    const qs = new URLSearchParams({ page: orderPage, per_page: orderSize });
    if (orderKeyword) qs.set("keyword", orderKeyword);
    const { status, data } = await api("/api/orders?" + qs.toString());

    if (status === 401) {
        location.href = "/login";
        return;
    }
    if (data.code !== 0) {
        list.innerHTML = `<div class="empty-state">加载失败</div>`;
        pagerBox.style.display = "none";
        return;
    }

    const pd = data.data;  // 分页结构：{items, total, page, per_page, total_pages}
    if (!pd.total) {
        // 有关键字时是"搜不到"，不再引导去购物
        if (orderKeyword) {
            list.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🔍</div>
                <p>没有匹配「${escapeHtml(orderKeyword)}」的订单</p>
                <button class="btn-secondary" onclick="clearOrderSearch()">清空搜索条件</button>
            </div>`;
        } else {
            list.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📦</div>
                <p>还没有订单</p>
                <button class="btn-primary" onclick="location.href='/'">去购物</button>
            </div>`;
        }
        pagerBox.style.display = "none";
        return;
    }
    if (!pd.items.length) {
        list.innerHTML = `<div class="empty-state">本页没有订单，请点下方分页栏翻页</div>`;
        renderPager(pagerBox, pd, goOrder);
        return;
    }

    list.innerHTML = pd.items.map((o) => {
        const st = STATUS_MAP[o.status] || { text: o.status, color: "#636e72" };
        const items = o.items.map(it => `
            <div class="order-item">
                <span>${escapeHtml(it.product_name)} × ${it.quantity}</span>
                <span>¥${(it.price * it.quantity).toFixed(2)}</span>
            </div>
        `).join("");
        // 收货地址（下单时的快照：老订单没快照则不显示）
        const addr = o.address_snapshot;
        const addrBlock = addr ? `
            <div class="order-addr">
                📍 <b>${escapeHtml(addr.receiver_name)}</b> ${escapeHtml(addr.receiver_phone)}
                <span class="order-addr-detail">${escapeHtml(addr.region)} ${escapeHtml(addr.detail)}</span>
            </div>` : "";
        // 物流信息：已发货/已完成显示物流公司+运单号，轨迹时间线由详情接口异步填充
        const shipped = ["shipped", "completed"].includes(o.status);
        const logisticsBlock = (shipped && o.logistics_company) ? `
            <div class="order-logistics">
                🚚 ${escapeHtml(o.logistics_company)} · 运单号：${escapeHtml(o.tracking_no || "")}
                <div class="track-timeline" id="tracks-${o.id}">
                    <div class="track-loading">物流轨迹加载中…</div>
                </div>
            </div>` : "";
        // 售后状态条：最新售后单存在时显示在物流区块附近（黄审核中/绿已同意/红已拒绝）
        const as = o.aftersale;  // 后端返回最新一张售后单，无则 null
        const aftersaleBlock = as ? renderAfterSaleBar(as) : "";
        // 评价区块（仅 completed 订单）：未评价显示【评价】按钮；已评价先给占位，
        // 列表渲染后异步拉评价详情填星级（与物流轨迹同一模式，避免列表接口过重）
        const reviewBlock = o.status === "completed"
            ? `<div class="order-review" id="review-${o.id}">
                   ${o.reviewed
                      ? '<span style="color:#b2bec3">评价加载中…</span>'
                      : `<button class="btn-primary" onclick="openReviewModal(${o.id})">评价</button>`}
               </div>`
            : "";
        // 操作按钮：pay/cancel 按后端返回的 actions 渲染；
        // confirm（确认收货）和申请售后不是纯状态机按钮，要结合最新售后单状态决定显隐
        const btns = (o.actions || []).map(a => {
            if (a === "pay") {
                return `<button class="btn-primary" onclick="orderAct(${o.id},'pay')">去支付</button>`;
            }
            if (a === "cancel") {
                return `<button class="btn-secondary" onclick="orderAct(${o.id},'cancel')">取消订单</button>`;
            }
            return "";
        });
        // 已发货(shipped)：确认收货 + 申请售后两个按钮的显隐规则——
        //   最新售后 pending(审核中) / approved(已同意)：两个按钮都隐藏（只显示状态条）
        //   最新售后 rejected(已拒绝) 或从未申请：两个按钮同时显示
        // 已完成(completed)：不显示申请售后按钮（入口关闭，后端也会 400），
        //   显示交易完成文案
        const asBlocked = as && (as.status === "pending" || as.status === "approved");
        const showConfirm = o.status === "shipped" && !asBlocked;
        const showAftersaleBtn = o.status === "shipped" && !asBlocked;
        if (showConfirm) {
            btns.push(`<button class="btn-primary" onclick="orderAct(${o.id},'confirm')">确认收货</button>`);
        }
        if (showAftersaleBtn) {
            btns.push(`<button class="btn-secondary" onclick="openAfterSaleModal(${o.id})">申请售后</button>`);
        }
        const doneTip = o.status === "completed"
            ? `<span style="color:#0984e3;font-size:13px;margin-right:8px">交易已完成，感谢您的购买</span>`
            : "";
        return `
            <div class="order-card">
                <div class="order-header">
                    <span>订单号：#${o.id}</span>
                    <span>下单时间：${new Date(o.created_at).toLocaleString("zh-CN")}</span>
                    <span class="order-status" style="background:${st.color}">${st.text}</span>
                </div>
                ${items}
                ${addrBlock}
                ${logisticsBlock}
                ${aftersaleBlock}
                ${reviewBlock}
                <div class="order-footer">
                    合计：<span style="color:#ff4757;font-weight:700;font-size:20px;margin-right:16px">¥${o.total_amount.toFixed(2)}</span>
                    ${doneTip}
                    ${btns.join(" ")}
                </div>
            </div>
        `;
    }).join("");

    // 已发货订单：拉详情补物流轨迹（详情接口才返回 tracks），纵向时间线最新在最上
    pd.items.forEach(o => {
        if (["shipped", "completed"].includes(o.status) && o.logistics_company) {
            loadTracks(o.id);
        }
    });

    // 已完成且已评价的订单：异步拉评价详情，在卡片上显示星级 + 查看评价
    pd.items.forEach(o => {
        if (o.status === "completed" && o.reviewed) {
            loadOrderReviewBar(o.id);
        }
    });

    renderPager(pagerBox, pd, goOrder);
}

// 已评价订单：拉单条评价，渲染「★x.x + 查看评价」（拉失败静默隐藏占位）
async function loadOrderReviewBar(oid) {
    const box = document.getElementById(`review-${oid}`);
    if (!box) return;
    const { data } = await api(`/api/orders/${oid}/reviews`);
    if (data.code !== 0 || !data.data || !box) return;
    const r = data.data;
    box.innerHTML = `
        <span class="rv-stars">${renderStars(r.rating)}</span>
        <span class="rv-score">${r.rating}.0 分</span>
        <button class="btn-secondary" onclick="openReviewViewModal(${oid})">查看评价</button>
    `;
    // 缓存评价详情，查看弹窗直接用，少一次请求
    reviewCache[oid] = r;
}

// 星级字符串：整星用 ★，其余用 ☆（订单级评价只允许整数评分）
function renderStars(rating) {
    const n = Number(rating) || 0;
    return "★".repeat(n) + "☆".repeat(Math.max(0, 5 - n));
}

// 售后状态条：审核中(黄)/已同意(绿)/已拒绝(红)，与状态标签风格一致
function renderAfterSaleBar(as) {
    const st = AS_STATUS_MAP[as.status] || { text: as.status_text || "售后", cls: "as-pending" };
    const typeText = escapeHtml(as.type_text || AS_TYPE_TEXT[as.type] || as.type);
    let extra = "";
    if (as.status === "pending") {
        extra = `<div class="as-extra">原因：${escapeHtml(as.reason || "")}</div>`;
    } else if (as.status === "rejected" && as.admin_note) {
        extra = `<div class="as-extra">管理员备注：${escapeHtml(as.admin_note)}</div>`;
    }
    return `
        <div class="order-aftersale ${st.cls}">
            <span class="as-tag">${st.text}</span>
            <span class="as-type">（${typeText}）</span>
            ${extra}
        </div>`;
}

async function loadTracks(oid) {
    const box = document.getElementById(`tracks-${oid}`);
    if (!box) return;
    const { data } = await api(`/api/orders/${oid}`);
    if (data.code !== 0 || !box) return;
    // 后端按 track_time 正序返回，展示时倒序——最新在最上
    const tracks = (data.data.tracks || []).slice().reverse();
    if (!tracks.length) {
        box.innerHTML = `<div class="track-loading">暂无物流轨迹</div>`;
        return;
    }
    box.innerHTML = tracks.map((t, i) => `
        <div class="track-item ${i === 0 ? "latest" : ""}">
            <span class="track-dot"></span>
            <div class="track-body">
                <div class="track-status">${escapeHtml(t.status)}${i === 0 ? '<span class="track-now">最新</span>' : ""}</div>
                <div class="track-info">${escapeHtml(t.info || "")}</div>
                <div class="track-time">${new Date(t.track_time).toLocaleString("zh-CN", { hour12: false })}</div>
            </div>
        </div>
    `).join("");
}

// ---------- 申请售后弹窗（已发货订单：确认收货前可申请；被拒绝后可重新申请） ----------
let aftersaleOrderId = null;
let aftersaleType = "refund_return";  // 默认选中退货退款

function openAfterSaleModal(oid) {
    aftersaleOrderId = oid;
    aftersaleType = "refund_return";
    document.getElementById("as-reason").value = "";
    // 重置类型按钮选中态
    document.querySelectorAll(".as-type-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.type === "refund_return");
    });
    document.getElementById("aftersale-modal").classList.add("show");
}

function closeAfterSaleModal() {
    aftersaleOrderId = null;
    document.getElementById("aftersale-modal").classList.remove("show");
}

function pickAfterSaleType(type, btn) {
    aftersaleType = type;
    document.querySelectorAll(".as-type-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
}

async function submitAfterSale() {
    if (aftersaleOrderId === null) return;
    const reason = document.getElementById("as-reason").value.trim();
    if (!reason) { toast("请填写申请原因", "error"); return; }
    const { data } = await api(`/api/orders/${aftersaleOrderId}/aftersale`, {
        method: "POST",
        body: { type: aftersaleType, reason }
    });
    if (data.code === 0) {
        toast(data.msg || "售后申请已提交", "success");
        closeAfterSaleModal();
        await loadOrders();
    } else {
        toast(data.msg || "提交失败", "error");
    }
}

// ---------- 商品评价（completed 订单：发表评价 / 查看自己的评价） ----------
let reviewOrderId = null;   // 当前打开评价弹窗的订单 id
let reviewRating = 0;       // 当前选中的星级（0=未选，必须 >=1 才允许提交）
const reviewCache = {};     // oid -> 评价详情（查看弹窗直接读缓存）

function openReviewModal(oid) {
    reviewOrderId = oid;
    reviewRating = 0;
    document.getElementById("rv-content").value = "";
    updateReviewCounter();
    paintReviewStars(0);
    document.getElementById("rv-pick-tip").textContent = "点击星星打分";
    document.getElementById("review-modal").classList.add("show");
}

function closeReviewModal() {
    reviewOrderId = null;
    reviewRating = 0;
    document.getElementById("review-modal").classList.remove("show");
}

// 点选星星：1-5 整数，选中后 1..n 亮金色
function pickReviewStar(n) {
    reviewRating = n;
    paintReviewStars(n);
    const labels = { 1: "很差", 2: "较差", 3: "一般", 4: "满意", 5: "非常满意" };
    document.getElementById("rv-pick-tip").textContent = labels[n] || "";
}

function paintReviewStars(n) {
    document.querySelectorAll("#rv-picker .star-item").forEach(el => {
        el.classList.toggle("on", Number(el.dataset.star) <= n);
    });
}

// 字数提示（textarea 已用 maxlength=500 硬限制，这里同步计数）
function updateReviewCounter() {
    const len = document.getElementById("rv-content").value.trim().length;
    document.getElementById("rv-count").textContent = len;
}

async function submitReview() {
    if (reviewOrderId === null) return;
    if (reviewRating < 1) { toast("请先点击星星打分", "error"); return; }
    const content = document.getElementById("rv-content").value.trim();
    if (!content) { toast("请填写评价内容", "error"); return; }
    if (content.length > 500) { toast("评价内容最多500字", "error"); return; }

    const { data } = await api(`/api/orders/${reviewOrderId}/reviews`, {
        method: "POST",
        body: { rating: reviewRating, content }
    });
    if (data.code === 0) {
        toast(data.msg || "评价成功", "success");
        reviewCache[reviewOrderId] = data.data;
        closeReviewModal();
        await loadOrders();   // 刷新订单列表：评价按钮变为星级
    } else {
        toast(data.msg || "评价提交失败", "error");
    }
}

// 查看自己的评价：优先读缓存，没有则请求接口
async function openReviewViewModal(oid) {
    let r = reviewCache[oid];
    if (!r) {
        const { data } = await api(`/api/orders/${oid}/reviews`);
        if (data.code !== 0 || !data.data) {
            toast("暂无评价", "error");
            return;
        }
        r = data.data;
        reviewCache[oid] = r;
    }
    const products = (r.product_names || []).map(n => escapeHtml(n)).join("、");
    document.getElementById("rv-view-body").innerHTML = `
        <div><span class="rv-stars">${renderStars(r.rating)}</span>
              <span class="rv-score">${r.rating}.0 分</span></div>
        <div class="rv-view-products">购买商品：${products || "-"}</div>
        <div class="rv-view-content">${escapeHtml(r.content)}</div>
        <div class="rv-view-time">评价时间：${new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}</div>
    `;
    document.getElementById("review-view-modal").classList.add("show");
}

function closeReviewViewModal() {
    document.getElementById("review-view-modal").classList.remove("show");
}

loadOrders();
