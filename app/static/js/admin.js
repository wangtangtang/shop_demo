// ========== 后台管理页 ==========
const STATUS_CLASS = {
    pending: "st-pending", paid: "st-paid", shipped: "st-shipped",
    completed: "st-completed", cancelled: "st-cancelled"
};
// 售后单状态样式（黄=审核中 / 绿=已同意 / 红=已拒绝）
const AS_STATUS_CLASS = {
    pending: "as-pending", approved: "as-approved", rejected: "as-rejected"
};
// 售后 AI 初审建议徽标样式（approve=绿 / reject=红 / manual=灰）
const AS_AI_CLASS = {
    approve: "ai-approve", reject: "ai-reject", manual: "ai-manual"
};
// 支付渠道展示（待支付订单 pay_channel 为空，显示 "-"）
const PAY_CHANNEL_TEXT = { wechat: "微信支付", alipay: "支付宝支付" };
// 顶部状态筛选按钮（空字符串 = 全部）
const FILTERS = [
    { key: "", text: "全部" },
    { key: "pending", text: "待支付" },
    { key: "paid", text: "待发货" },
    { key: "shipped", text: "已发货" },
    { key: "completed", text: "已完成" },
    { key: "cancelled", text: "已取消" }
];
// 售后审核页的状态筛选按钮
const AS_FILTERS = [
    { key: "", text: "全部" },
    { key: "pending", text: "审核中" },
    { key: "approved", text: "已同意" },
    { key: "rejected", text: "已拒绝" }
];
let currentFilter = "";
let asFilter = "";               // 售后列表当前状态筛选
let adminOrderPage = 1;      // 后台订单当前页
let adminOrderSize = 10;     // 后台订单每页条数
let adminProductPage = 1;    // 后台商品当前页
let adminProductSize = 10;   // 后台商品每页条数
let adminAftersalePage = 1;  // 后台售后当前页
let adminAftersaleSize = 10; // 后台售后每页条数
let adminReviewPage = 1;     // 后台评价当前页
let adminReviewSize = 10;    // 后台评价每页条数
let reviewFilter = "all";    // 评价状态筛选：all 全部(含已删除) / deleted 只看已删除
let reviewLoaded = false;    // 评价 tab 是否首次加载（切到该 tab 才请求，省一次列表开销）
// 列表的搜索关键字（空串 = 不搜索；只在点搜索/回车时更新）
let adminOrderKeyword = "";
let adminProductKeyword = "";
let adminAftersaleKeyword = "";
let adminReviewKeyword = "";
let rejectAftersaleId = null;  // 拒绝弹窗当前处理的售后单 id

function fmtTime(iso) {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

function switchTab(name, btn) {
    document.querySelectorAll(".admin-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".admin-panel").forEach(p => p.classList.remove("active"));
    // 兼容老写法 onclick="switchTab('orders')"：没传按钮时按 data/文本兜底
    const target = btn || (event && event.target) || document.querySelector(".admin-tab");
    if (target && target.classList) target.classList.add("active");
    document.getElementById("panel-" + name).classList.add("active");
    // 评价列表懒加载：第一次切到评价 tab 才请求（与订单/商品页同时加载相比省一次开销）
    if (name === "reviews" && !reviewLoaded) {
        reviewLoaded = true;
        loadAdminReviews();
    }
    // 数据分析看板懒加载：第一次切到「📊 数据分析」才请求全部统计接口
    if (name === "dashboard" && !dashboardLoaded) {
        dashboardLoaded = true;
        loadDashboard();
    }
}

// ---------- 订单管理 ----------
function renderFilterBar() {
    document.getElementById("status-filter").innerHTML = FILTERS.map(f => `
        <button class="filter-btn ${f.key === currentFilter ? "active" : ""}"
                onclick="filterOrders('${f.key}')">${f.text}</button>
    `).join("");
}

async function filterOrders(status) {
    currentFilter = status;
    adminOrderPage = 1;  // 切换状态筛选后回到第 1 页
    renderFilterBar();
    await loadAdminOrders();
}

// 订单搜索：读输入框（去空格）→ 更新关键字并回到第 1 页；清空输入即恢复全量
async function searchAdminOrders() {
    adminOrderKeyword = document.getElementById("admin-order-keyword").value.trim();
    adminOrderPage = 1;
    await loadAdminOrders();
}

async function goAdminOrder(page, size) {
    adminOrderPage = page;
    if (size) adminOrderSize = size;
    await loadAdminOrders();
}

async function loadAdminOrders() {
    const qs = new URLSearchParams({ page: adminOrderPage, per_page: adminOrderSize });
    if (currentFilter) qs.set("status", currentFilter);
    if (adminOrderKeyword) qs.set("keyword", adminOrderKeyword);
    const { status, data } = await api("/api/admin/orders?" + qs.toString());
    const body = document.getElementById("admin-order-body");
    const pagerBox = document.getElementById("admin-order-pager");

    // 订单表格 9 列：订单号/顾客/商品/金额/状态/支付渠道/收货物流/下单时间/操作 → colspan 用 9
    const failRow = (msg, color = "#b2bec3") =>
        `<tr><td colspan="9" style="text-align:center;color:${color}">${msg}</td></tr>`;

    if (status === 401) {
        body.innerHTML = failRow("请先在右上角登录管理员账号（admin / admin123）", "#ff4757");
        pagerBox.style.display = "none";
        document.getElementById("admin-tip").style.display = "block";
        return;
    }
    if (status === 403) {
        body.innerHTML = failRow("当前账号不是管理员。请退出后用 admin / admin123 登录", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (data.code !== 0) {
        body.innerHTML = failRow("加载失败");
        pagerBox.style.display = "none";
        return;
    }
    const pd = data.data;  // 分页结构：{items, total, page, per_page, total_pages}
    if (!pd.items.length) {
        const emptyMsg = adminOrderKeyword ? "没有匹配的订单" : "没有订单";
        body.innerHTML = failRow(pd.total_pages > 0 ? "本页没有数据" : emptyMsg);
    } else {
        body.innerHTML = pd.items.map(o => {
            const items = o.items.map(it =>
                `${it.product_name} ×${it.quantity}（¥${(it.price * it.quantity).toFixed(2)}）`
            ).join("<br>");
            // 只有「待发货」的订单显示发货按钮
            const shipBtn = o.status === "paid"
                ? `<button class="admin-btn" onclick="openShipModal(${o.id})">发货</button>`
                : `<button class="admin-btn" disabled>发货</button>`;
            // 支付渠道：wechat→微信支付，alipay→支付宝支付，待支付（空）→ -
            const payChannel = PAY_CHANNEL_TEXT[o.pay_channel] || "-";
            // 收货人/电话来自下单快照；已发货后显示物流公司+运单号
            const snap = o.address_snapshot;
            const receiver = snap
                ? `${escapeHtml(snap.receiver_name)} ${escapeHtml(snap.receiver_phone)}`
                : "—";
            const logistics = o.logistics_company
                ? `<div style="color:#6c5ce7;font-size:12px;margin-top:4px;line-height:1.6">🚚 ${escapeHtml(o.logistics_company)}<br>单号：${escapeHtml(o.tracking_no || "")}</div>`
                : `<div style="color:#b2bec3;font-size:12px;margin-top:4px;">待发货</div>`;
            // 有售后单的订单：状态列追加售后小标签
            const asBadge = o.aftersale
                ? `<div style="margin-top:4px"><span class="st-badge ${AS_STATUS_CLASS[o.aftersale.status] || ""}">售后：${escapeHtml(o.aftersale.status_text)}</span></div>`
                : "";
            return `
                <tr>
                    <td>#${o.id}</td>
                    <td>${o.username || ("用户" + o.user_id)}</td>
                    <td>${items}</td>
                    <td style="color:#ff4757;font-weight:700">¥${o.total_amount.toFixed(2)}</td>
                    <td><span class="st-badge ${STATUS_CLASS[o.status] || ""}">${o.status_text}</span>${asBadge}</td>
                    <td style="white-space:nowrap;color:#636e72">${payChannel}</td>
                    <td style="font-size:12.5px;color:#636e72;line-height:1.5;white-space:normal">${receiver}${logistics}</td>
                    <td style="white-space:nowrap">${fmtTime(o.created_at)}</td>
                    <td>${shipBtn}</td>
                </tr>
            `;
        }).join("");
    }
    renderPager(pagerBox, pd, goAdminOrder);
}

// ---------- 发货弹窗：填写物流公司+运单号后提交 ----------
let shipOrderId = null;
function openShipModal(oid) {
    shipOrderId = oid;
    // 重置表单
    document.getElementById("ship-company").value = "";
    document.getElementById("ship-trackno").value = "";
    document.getElementById("ship-modal").classList.add("show");
}
function closeShipModal() {
    shipOrderId = null;
    document.getElementById("ship-modal").classList.remove("show");
}
async function confirmShip() {
    if (shipOrderId === null) return;
    const logisticsCompany = document.getElementById("ship-company").value.trim();
    const trackingNo = document.getElementById("ship-trackno").value.trim();
    if (!logisticsCompany) { toast("请选择物流公司", "error"); return; }
    if (!trackingNo) { toast("请输入运单号", "error"); return; }
    const { data } = await api(`/api/admin/orders/${shipOrderId}/ship`, {
        method: "POST",
        body: { logistics_company: logisticsCompany, tracking_no: trackingNo }
    });
    if (data.code === 0) {
        toast(`订单 #${shipOrderId} 已发货`, "success");
        closeShipModal();
        loadAdminOrders();
    } else {
        toast(data.msg || "发货失败", "error");
    }
}

// ---------- 售后审核（分页 + 状态筛选） ----------
function renderAftersaleFilterBar() {
    document.getElementById("aftersale-filter").innerHTML = AS_FILTERS.map(f => `
        <button class="filter-btn ${f.key === asFilter ? "active" : ""}"
                onclick="filterAftersales('${f.key}')">${f.text}</button>
    `).join("");
}

async function filterAftersales(status) {
    asFilter = status;
    adminAftersalePage = 1;  // 切换状态筛选后回到第 1 页
    renderAftersaleFilterBar();
    await loadAdminAftersales();
}

// 售后搜索：读输入框（去空格）→ 更新关键字并回到第 1 页；清空输入即恢复全量
async function searchAdminAftersales() {
    adminAftersaleKeyword = document.getElementById("admin-aftersale-keyword").value.trim();
    adminAftersalePage = 1;
    await loadAdminAftersales();
}

async function goAdminAftersale(page, size) {
    adminAftersalePage = page;
    if (size) adminAftersaleSize = size;
    await loadAdminAftersales();
}

async function loadAdminAftersales() {
    const qs = new URLSearchParams({ page: adminAftersalePage, per_page: adminAftersaleSize });
    if (asFilter) qs.set("status", asFilter);
    if (adminAftersaleKeyword) qs.set("keyword", adminAftersaleKeyword);
    const { status, data } = await api("/api/admin/aftersales?" + qs.toString());
    const body = document.getElementById("admin-aftersale-body");
    const pagerBox = document.getElementById("admin-aftersale-pager");

    // 售后表格 7 列：订单号/用户/类型/原因/申请时间/状态/操作 → colspan 用 7
    const failRow = (msg, color = "#b2bec3") =>
        `<tr><td colspan="7" style="text-align:center;color:${color}">${msg}</td></tr>`;

    if (status === 401) {
        body.innerHTML = failRow("请先登录管理员账号", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (status === 403) {
        body.innerHTML = failRow("当前账号不是管理员", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (data.code !== 0) {
        body.innerHTML = failRow("加载失败");
        pagerBox.style.display = "none";
        return;
    }

    const pd = data.data;  // 分页结构：{items, total, page, per_page, total_pages}
    if (!pd.items.length) {
        const emptyMsg = adminAftersaleKeyword ? "没有匹配的售后申请" : "没有售后申请";
        body.innerHTML = failRow(pd.total_pages > 0 ? "本页没有数据" : emptyMsg);
    } else {
        body.innerHTML = pd.items.map(a => {
            // pending 行显示【同意】【拒绝】；已处理显示结果 + 管理员备注
            const actionCell = a.status === "pending" ? `
                <button class="admin-btn green" onclick="approveAftersale(${a.id})">同意</button>
                <button class="admin-btn red" onclick="openRejectModal(${a.id})">拒绝</button>`
                : (a.status === "approved"
                    ? `<span style="color:#00b894;font-weight:600;white-space:nowrap">已同意</span>`
                    : `<span style="color:#ff4757;font-weight:600;white-space:nowrap">已拒绝</span>`);
            const note = a.admin_note
                ? `<div style="color:#636e72;font-size:12px;margin-top:4px">备注：${escapeHtml(a.admin_note)}</div>`
                : "";
            // AI 初审建议：只是参考意见，徽标文案带「AI建议」前缀，和真正的售后状态标签明确区分；
            // 同意/拒绝按钮逻辑完全不变——最终状态永远以人工点击为准（老工单无建议则不显示）
            const ai = a.ai_suggestion
                ? `<div class="ai-sugg"><span class="ai-sugg-tag ${AS_AI_CLASS[a.ai_suggestion] || "ai-manual"}">AI建议：${escapeHtml(a.ai_suggestion_text || "人工核实")}</span>`
                  + `<span class="ai-sugg-reason">${escapeHtml(a.ai_reason || "")}</span>`
                  + `<span class="ai-sugg-note">（系统初审建议，仅供参考，需人工裁定）</span></div>`
                : "";
            const amount = a.order_amount != null ? `¥${Number(a.order_amount).toFixed(2)}` : "—";
            return `
                <tr>
                    <td style="white-space:nowrap">#${a.order_id}<br><span style="color:#b2bec3;font-size:12px">${amount}</span></td>
                    <td>${escapeHtml(a.username || ("用户" + a.user_id))}</td>
                    <td style="white-space:nowrap">${escapeHtml(a.type_text)}</td>
                    <td style="white-space:normal;max-width:280px">${escapeHtml(a.reason || "")}${ai}${note}</td>
                    <td style="white-space:nowrap">${fmtTime(a.created_at)}</td>
                    <td><span class="st-badge ${AS_STATUS_CLASS[a.status] || ""}">${a.status_text}</span></td>
                    <td style="white-space:nowrap">${actionCell}</td>
                </tr>
            `;
        }).join("");
    }
    renderPager(pagerBox, pd, goAdminAftersale);
}

async function approveAftersale(aid) {
    if (!confirm("确定同意这张售后申请吗？")) return;
    const { data } = await api(`/api/admin/aftersales/${aid}/handle`, {
        method: "POST",
        body: { action: "approve" }
    });
    if (data.code === 0) {
        toast("已同意售后申请", "success");
        loadAdminAftersales();
        loadAdminOrders();  // 订单表格里的售后小标签同步刷新
    } else {
        toast(data.msg || "操作失败", "error");
    }
}

// ---------- 拒绝弹窗：可填备注（可留空） ----------
function openRejectModal(aid) {
    rejectAftersaleId = aid;
    document.getElementById("reject-note").value = "";
    document.getElementById("reject-modal").classList.add("show");
}
function closeRejectModal() {
    rejectAftersaleId = null;
    document.getElementById("reject-modal").classList.remove("show");
}
async function confirmRejectAftersale() {
    if (rejectAftersaleId === null) return;
    const note = document.getElementById("reject-note").value.trim();
    const { data } = await api(`/api/admin/aftersales/${rejectAftersaleId}/handle`, {
        method: "POST",
        body: { action: "reject", note }
    });
    if (data.code === 0) {
        toast("已拒绝售后申请", "success");
        closeRejectModal();
        loadAdminAftersales();
        loadAdminOrders();  // 订单表格里的售后小标签同步刷新
    } else {
        toast(data.msg || "操作失败", "error");
    }
}

// ---------- 评价管理（分页 + 状态筛选 + 关键字搜索 + 软删除） ----------
function filterReviews(status, btn) {
    reviewFilter = status;
    adminReviewPage = 1;  // 切换筛选后回到第 1 页
    document.querySelectorAll("#review-filter .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    loadAdminReviews();
}

// 评价搜索：读输入框（去空格）→ 更新关键字并回到第 1 页
async function searchAdminReviews() {
    adminReviewKeyword = document.getElementById("admin-review-keyword").value.trim();
    adminReviewPage = 1;
    await loadAdminReviews();
}

async function goAdminReview(page, size) {
    adminReviewPage = page;
    if (size) adminReviewSize = size;
    await loadAdminReviews();
}

async function loadAdminReviews() {
    const qs = new URLSearchParams({ page: adminReviewPage, per_page: adminReviewSize });
    if (reviewFilter && reviewFilter !== "all") qs.set("status", reviewFilter);
    if (adminReviewKeyword) qs.set("keyword", adminReviewKeyword);
    const { status, data } = await api("/api/admin/reviews?" + qs.toString());
    const body = document.getElementById("admin-review-body");
    const pagerBox = document.getElementById("admin-review-pager");

    // 评价表格 7 列：订单号/用户/评分/内容/时间/状态/操作 → colspan 用 7
    const failRow = (msg, color = "#b2bec3") =>
        `<tr><td colspan="7" style="text-align:center;color:${color}">${msg}</td></tr>`;

    if (status === 401) {
        body.innerHTML = failRow("请先登录管理员账号", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (status === 403) {
        body.innerHTML = failRow("当前账号不是管理员", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (data.code !== 0) {
        body.innerHTML = failRow("加载失败");
        pagerBox.style.display = "none";
        return;
    }

    const pd = data.data;
    if (!pd.items.length) {
        const emptyMsg = adminReviewKeyword
            ? "没有匹配的评价"
            : (reviewFilter === "deleted" ? "没有已删除的评价" : "没有评价");
        body.innerHTML = failRow(pd.total_pages > 0 ? "本页没有数据" : emptyMsg);
    } else {
        body.innerHTML = pd.items.map(r => {
            const deleted = r.is_deleted === 1;
            // 已删除评价整行标红灰，内容加删除线；正常评价黑色
            const rowStyle = deleted ? ' style="background:#fff5f5"' : "";
            const contentStyle = deleted
                ? 'style="color:#ff4757;text-decoration:line-through"'
                : 'style="color:#2d3436"';
            const statusCell = deleted
                ? '<span class="st-badge as-rejected">已删除</span>'
                : '<span class="st-badge st-completed">正常</span>';
            // 删除是幂等的：已删除的只显示灰色「已删除」按钮，不重复调接口
            const actionCell = deleted
                ? '<button class="admin-btn gray" disabled>已删除</button>'
                : `<button class="admin-btn red" onclick="deleteReview(${r.id})">删除</button>`;
            const stars = "★".repeat(r.rating) + "☆".repeat(5 - r.rating);
            return `
                <tr${rowStyle}>
                    <td style="white-space:nowrap">#${r.order_id}</td>
                    <td style="white-space:nowrap">${escapeHtml(r.username || ("用户" + r.user_id))}</td>
                    <td style="white-space:nowrap;color:#faad14;letter-spacing:1px">${stars}</td>
                    <td style="white-space:normal;max-width:320px;line-height:1.6" ${contentStyle}>${escapeHtml(r.content)}</td>
                    <td style="white-space:nowrap">${fmtTime(r.created_at)}</td>
                    <td>${statusCell}</td>
                    <td style="white-space:nowrap">${actionCell}</td>
                </tr>
            `;
        }).join("");
    }
    renderPager(pagerBox, pd, goAdminReview);
}

async function deleteReview(rid) {
    if (!confirm("确定删除这条评价吗？\n删除后顾客端商品评价列表和评分汇总立刻不再展示，后台仍可在「已删除」中查到（软删除，可追溯）。")) return;
    const { data } = await api(`/api/admin/reviews/${rid}/delete`, { method: "POST" });
    if (data.code === 0) {
        toast("评价已删除", "success");
        loadAdminReviews();
    } else {
        toast(data.msg || "删除失败", "error");
    }
}

// ---------- 商品管理（分页 + 搜索） ----------
async function goAdminProduct(page, size) {
    adminProductPage = page;
    if (size) adminProductSize = size;
    await loadAdminProducts();
}

// 商品搜索：读输入框（去空格）→ 更新关键字并回到第 1 页；清空输入即恢复全量
async function searchAdminProducts() {
    adminProductKeyword = document.getElementById("admin-product-keyword").value.trim();
    adminProductPage = 1;
    await loadAdminProducts();
}

async function loadAdminProducts() {
    // 后台列表接口：在售+已下架都返回（顾客端 /api/products 看不到已下架的）
    const qs = new URLSearchParams({ page: adminProductPage, per_page: adminProductSize });
    if (adminProductKeyword) qs.set("keyword", adminProductKeyword);
    const { status, data } = await api("/api/admin/products?" + qs.toString());
    const body = document.getElementById("admin-product-body");
    const pagerBox = document.getElementById("admin-product-pager");
    // 商品表格 8 列：ID/图片/商品名/价格/库存/描述图片/状态/操作
    const failRow = (msg, color = "#b2bec3") =>
        `<tr><td colspan="8" style="text-align:center;color:${color}">${msg}</td></tr>`;

    if (status === 401) {
        body.innerHTML = failRow("请先登录管理员账号", "#ff4757");
        pagerBox.style.display = "none";
        return;
    }
    if (data.code !== 0) {
        body.innerHTML = failRow("加载失败");
        pagerBox.style.display = "none";
        return;
    }

    const pd = data.data;  // 分页结构：{items, total, page, per_page, total_pages}
    if (!pd.items.length) {
        const emptyMsg = adminProductKeyword ? "没有匹配的商品" : "没有商品";
        body.innerHTML = failRow(pd.total_pages > 0 ? "本页没有数据" : emptyMsg);
    } else {
        body.innerHTML = pd.items.map(p => {
            const off = p.is_deleted === 1;
            const rowStyle = off ? ` style="opacity:0.55;background:#f5f6fa"` : "";
            const statusCell = off
                ? `<td style="color:#e17055;font-weight:600;white-space:nowrap">已下架</td>`
                : `<td style="color:#00b894;font-weight:600;white-space:nowrap">在售</td>`;
            const actionCell = off
                ? `<button class="admin-btn green" onclick="restoreProduct(${p.id})">恢复上架</button>`
                : `<button class="admin-btn" onclick="updateProduct(${p.id})">保存</button>
                    <button class="admin-btn red" onclick="deleteProduct(${p.id})">删除</button>`;
            // 表格缩略图只显示第一张（多图用英文逗号分隔，详情页轮播展示）
            const firstImg = (p.images && p.images.length) ? p.images[0] : p.image;
            return `
            <tr${rowStyle}>
                <td>${p.id}</td>
                <td><img src="/static/images/${firstImg}" alt=""
                    style="width:42px;height:42px;object-fit:cover;border-radius:6px;border:1px solid #dfe6e9"
                    onerror="if(this.dataset.fb){this.style.display='none'}else{this.dataset.fb=1;this.src='/static/images/default.svg'}"></td>
                <td><input value="${p.name}" id="name-${p.id}" ${off ? "disabled" : ""} style="width:110px;padding:5px 8px;border:1px solid #dfe6e9;border-radius:5px"></td>
                <td><input value="${p.price}" id="price-${p.id}" type="number" step="0.01" ${off ? "disabled" : ""} style="width:75px;padding:5px 8px;border:1px solid #dfe6e9;border-radius:5px"></td>
                <td><input value="${p.stock}" id="stock-${p.id}" type="number" ${off ? "disabled" : ""} style="width:60px;padding:5px 8px;border:1px solid #dfe6e9;border-radius:5px"></td>
                <td>
                    <input value="${p.description || ""}" id="desc-${p.id}" ${off ? "disabled" : ""} style="width:150px;padding:5px 8px;border:1px solid #dfe6e9;border-radius:5px;margin-bottom:4px" placeholder="描述">
                    <input value="${p.image || ""}" id="image-${p.id}" ${off ? "disabled" : ""} style="width:150px;padding:5px 8px;border:1px solid #dfe6e9;border-radius:5px" placeholder="图片文件名，多张用英文逗号分隔">
                </td>
                ${statusCell}
                <td style="white-space:nowrap">${actionCell}</td>
            </tr>`;
        }).join("");
    }
    renderPager(pagerBox, pd, goAdminProduct);
}

async function addProduct() {
    const body = {
        name: document.getElementById("p-name").value.trim(),
        price: document.getElementById("p-price").value,
        stock: document.getElementById("p-stock").value || 0,
        description: document.getElementById("p-desc").value.trim(),
        image: document.getElementById("p-image").value.trim()
    };
    if (!body.name) { toast("商品名不能为空", "error"); return; }
    const { data } = await api("/api/admin/products", { method: "POST", body });
    if (data.code === 0) {
        toast("商品已添加", "success");
        ["p-name", "p-price", "p-stock", "p-desc", "p-image"].forEach(id => document.getElementById(id).value = "");
        adminProductPage = 1;  // 新增后回到第 1 页，立刻能看到新商品
        loadAdminProducts();
    } else {
        toast(data.msg || "添加失败", "error");
    }
}

async function updateProduct(pid) {
    const body = {
        name: document.getElementById(`name-${pid}`).value.trim(),
        price: document.getElementById(`price-${pid}`).value,
        stock: document.getElementById(`stock-${pid}`).value,
        description: document.getElementById(`desc-${pid}`).value,
        image: document.getElementById(`image-${pid}`).value.trim()
    };
    const { data } = await api(`/api/admin/products/${pid}`, { method: "PUT", body });
    if (data.code === 0) {
        toast(`商品 #${pid} 已保存`, "success");
        loadAdminProducts();
    } else {
        toast(data.msg || "保存失败", "error");
    }
}

async function deleteProduct(pid) {
    if (!confirm("确定下架这个商品吗？\n下架后顾客端立刻看不到、不能购买，数据保留可随时恢复；\n别人购物车里的这件商品会一并清掉，历史订单不受影响。")) return;
    const { data } = await api(`/api/admin/products/${pid}`, { method: "DELETE" });
    if (data.code === 0) {
        toast("商品已下架，可在列表中恢复", "success");
        loadAdminProducts();
    } else {
        toast(data.msg || "删除失败", "error");
    }
}

async function restoreProduct(pid) {
    const { data } = await api(`/api/admin/products/${pid}/restore`, { method: "POST" });
    if (data.code === 0) {
        toast(`商品 #${pid} 已恢复上架`, "success");
        loadAdminProducts();
    } else {
        toast(data.msg || "恢复失败", "error");
    }
}

// ========== 📊 数据分析看板（一期；普通 script，无 ES module，无任何 CDN/图表库） ==========
let dashboardLoaded = false;   // 看板是否已首次加载（切到 tab 才懒加载）
let dashboardDays = 7;         // 当前统计周期：7 / 30

// 千分位金额：¥1,280.00（后端已是 round 2，这里补千分位与固定两位）
function dbMoney(v) {
    const n = Number(v);
    if (!isFinite(n)) return "¥0.00";
    return "¥" + n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// 周期切换：高亮按钮 + 只刷新随周期变化的 4 块（指标卡/趋势/TOP/简报）
function switchDashboardDays(days, btn) {
    if (days === dashboardDays) return;
    dashboardDays = days;
    document.querySelectorAll(".db-day-bar .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("db-top-tag").textContent = "近 " + days + " 天";
    document.getElementById("db-brief-tag").textContent =
        "近 " + days + " 天 · 模板生成（离线可用）";
    document.getElementById("p2-review-tag").textContent =
        "近 " + days + " 天 · 离线规则引擎";
    loadDashboardPeriodParts();
    loadReviewInsights();
}

// 统一拉一个看板接口，返回 {ok, status, data}；网络/非 JSON 也不抛白屏
async function dbFetch(url) {
    try {
        const r = await api(url);
        return { ok: true, status: r.status, data: r.data || {} };
    } catch (e) {
        return { ok: false, status: 0, data: {} };
    }
}

// 401/403：在看板顶部给一次红色提示（各模块仍显示自己的失败占位）
function dbAuthGuard(status) {
    const tip = document.getElementById("db-auth-tip");
    if (status === 401) {
        tip.textContent = "请先在右上角登录管理员账号（admin / admin123）";
        tip.style.display = "block";
        return true;
    }
    if (status === 403) {
        tip.textContent = "当前账号不是管理员，看板仅对 admin 开放";
        tip.style.display = "block";
        return true;
    }
    return false;
}

// 首次进入：全量 6 个接口一起拉
async function loadDashboard() {
    await Promise.all([
        loadDashboardPeriodParts(),
        loadFunnel(),
        loadAiPanel(),
        loadReviewInsights(),
        loadInventoryAlerts(),
        loadRfm()
    ]);
}

// 随周期变化的 4 块：指标卡 / 趋势 / TOP5 / 简报
async function loadDashboardPeriodParts() {
    const d = dashboardDays;
    const [ov, tr, top, brief] = await Promise.all([
        dbFetch("/api/admin/stats/overview?days=" + d),
        dbFetch("/api/admin/stats/sales-trend?days=" + d),
        dbFetch("/api/admin/stats/top-products?days=" + d + "&limit=5"),
        dbFetch("/api/admin/stats/ai/briefing?days=" + d)
    ]);
    const bad = ov.status === 401 || ov.status === 403 || tr.status === 401 || tr.status === 403
        || top.status === 401 || top.status === 403 || brief.status === 401 || brief.status === 403;
    if (bad) {
        dbAuthGuard(ov.status || tr.status || top.status || brief.status);
    } else {
        document.getElementById("db-auth-tip").style.display = "none";
    }
    renderOverviewCards(ov);
    renderTrendChart(tr);
    renderTopProducts(top);
    renderBriefing(brief);
}

// 环比小角标：涨绿 ↗ / 跌红 ↘ / 持平灰 → / 无上期数据显示 –
function dbChangeHtml(pct) {
    if (pct === null || pct === undefined) return '<span class="db-change-flat">–</span>';
    const n = Number(pct);
    if (!isFinite(n)) return '<span class="db-change-flat">–</span>';
    if (n > 1) return '<span class="db-change-up">↗ ' + Math.abs(n).toFixed(1) + '%</span>';
    if (n < -1) return '<span class="db-change-down">↘ ' + Math.abs(n).toFixed(1) + '%</span>';
    return '<span class="db-change-flat">→ ' + Math.abs(n).toFixed(1) + '%</span>';
}

function dbFailBox(msg) {
    return '<div class="db-empty" style="color:#ff4757">' + escapeHtml(msg) + "</div>";
}

// ---------- 1. 六个核心指标卡 ----------
function renderOverviewCards(res) {
    const box = document.getElementById("db-cards");
    if (!res.ok || (res.status !== 200)) {
        box.innerHTML = dbFailBox("核心指标加载失败");
        return;
    }
    if (res.data.code !== 0) {
        box.innerHTML = dbFailBox(res.data.msg || "核心指标加载失败");
        return;
    }
    const d = res.data.data || {};
    const rating = (d.avg_rating === null || d.avg_rating === undefined)
        ? "暂无" : Number(d.avg_rating).toFixed(1) + " 分";
    const cards = [
        { label: "GMV（有效订单）", value: dbMoney(d.gmv), money: true,
          sub: "上期 " + dbMoney(d.gmv_prev) + " &nbsp; " + dbChangeHtml(d.gmv_change_pct) },
        { label: "下单量（含未支付/取消）", value: (d.order_count || 0) + " 单",
          sub: "上期 " + (d.order_count_prev || 0) + " 单 &nbsp; " + dbChangeHtml(d.order_count_change_pct) },
        { label: "客单价", value: dbMoney(d.avg_order_value), money: true, sub: "GMV / 有效订单数" },
        { label: "支付率", value: Number(d.pay_rate || 0).toFixed(1) + "%",
          sub: "有效 " + (d.valid_order_count || 0) + " / 全部 " + (d.order_count || 0) },
        { label: "售后率", value: Number(d.aftersale_rate || 0).toFixed(1) + "%",
          sub: "申请售后 " + (d.aftersale_count || 0) + " 单" },
        { label: "平均评分", value: rating, sub: "有效评价 " + (d.rating_count || 0) + " 条" }
    ];
    box.innerHTML = cards.map(c =>
        '<div class="db-stat-card">'
        + '<div class="db-label">' + c.label + "</div>"
        + '<div class="db-value' + (c.money ? " db-money" : "") + '">' + c.value + "</div>"
        + '<div class="db-sub">' + c.sub + "</div>"
        + "</div>"
    ).join("");
}

// ---------- 2. 销售趋势：内联 SVG 折线（GMV 一条线，<title> 悬浮明细） ----------
function renderTrendChart(res) {
    const box = document.getElementById("db-trend");
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        box.innerHTML = dbFailBox("销售趋势加载失败");
        return;
    }
    const rows = res.data.data || [];
    if (!rows.length) {
        box.innerHTML = '<div class="db-empty">暂无数据</div>';
        return;
    }
    const W = 900, H = 220, PAD_L = 56, PAD_R = 14, PAD_T = 16, PAD_B = 30;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const values = rows.map(r => Number(r.gmv) || 0);
    let maxV = Math.max.apply(null, values);
    if (!(maxV > 0)) maxV = 1;                 // 全 0 时也画一条贴底的线
    maxV = maxV * 1.1;                         // 顶部留 10% 空间
    const stepX = rows.length > 1 ? plotW / (rows.length - 1) : 0;
    const xy = values.map((v, i) => {
        const x = PAD_L + (rows.length > 1 ? stepX * i : plotW / 2);
        const y = PAD_T + plotH - (v / maxV) * plotH;
        return { x: x, y: y };
    });
    let svg = '<svg class="db-trend-svg" viewBox="0 0 ' + W + " " + H
        + '" preserveAspectRatio="none" role="img">';
    // 横向网格 4 条 + Y 轴金额刻度
    for (let i = 0; i <= 4; i++) {
        const y = PAD_T + plotH * i / 4;
        const val = maxV * (1 - i / 4);
        const label = val >= 1000 ? String(Math.round(val)) : val.toFixed(1);
        svg += '<line class="db-grid" x1="' + PAD_L + '" y1="' + y + '" x2="' + (W - PAD_R)
            + '" y2="' + y + '"/>';
        svg += '<text class="db-axis" x="' + (PAD_L - 6) + '" y="' + (y + 3)
            + '" text-anchor="end">¥' + label + "</text>";
    }
    // X 轴日期：点多时每 7 个左右标一个，避免挤在一起
    const tickEvery = Math.max(1, Math.ceil(rows.length / 7));
    rows.forEach((r, i) => {
        if (i % tickEvery === 0 || i === rows.length - 1) {
            svg += '<text class="db-axis" x="' + xy[i].x + '" y="' + (H - 8)
                + '" text-anchor="middle">' + String(r.date).slice(5) + "</text>";
        }
    });
    const points = xy.map(p => p.x.toFixed(1) + "," + p.y.toFixed(1)).join(" ");
    svg += '<polyline points="' + points + '"/>';
    // 每点一个小圆 + <title>（浏览器原生悬浮，零依赖）
    rows.forEach((r, i) => {
        svg += '<circle cx="' + xy[i].x.toFixed(1) + '" cy="' + xy[i].y.toFixed(1)
            + '" r="3"><title>' + r.date + "&#10;GMV：" + dbMoney(r.gmv)
            + "&#10;订单：" + (r.orders || 0) + " 单（有效 "
            + (r.paid_orders || 0) + " 单）</title></circle>";
    });
    svg += "</svg>";
    box.innerHTML = svg;
}

// ---------- 3. TOP5：CSS 横向条形，宽度按最大销量归一化 ----------
function renderTopProducts(res) {
    const box = document.getElementById("db-top");
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        box.innerHTML = dbFailBox("热销榜加载失败");
        return;
    }
    const rows = res.data.data || [];
    if (!rows.length) {
        box.innerHTML = '<div class="db-empty">本周期暂无成交</div>';
        return;
    }
    const maxQty = Math.max.apply(null, rows.map(r => Number(r.qty) || 0)) || 1;
    box.innerHTML = rows.map(r => {
        const qty = Number(r.qty) || 0;
        const widthPct = (qty / maxQty * 100).toFixed(1);
        return '<div class="db-bar-row">'
            + '<div class="db-bar-name" title="' + escapeHtml(r.product_name) + '">'
            + escapeHtml(r.product_name) + "</div>"
            + '<div class="db-bar-track"><div class="db-bar-fill" style="width:' + widthPct + '%"></div></div>'
            + '<div class="db-bar-meta">' + qty + ' 件 / <b>' + dbMoney(r.amount) + "</b></div>"
            + "</div>";
    }).join("");
}

// ---------- 4. 漏斗：5 级阶梯横条，宽度按注册数归一化，级间转化率 ----------
function loadFunnel() {
    dbFetch("/api/admin/stats/funnel").then(res => {
        const box = document.getElementById("db-funnel");
        if (res.status === 401 || res.status === 403) { dbAuthGuard(res.status); }
        if (!res.ok || res.status !== 200 || res.data.code !== 0) {
            box.innerHTML = dbFailBox("转化漏斗加载失败");
            return;
        }
        const rows = res.data.data || [];
        if (!rows.length) {
            box.innerHTML = '<div class="db-empty">暂无数据</div>';
            return;
        }
        const baseUsers = rows[0].users || 0;
        box.innerHTML = rows.map((r, i) => {
            const widthPct = baseUsers > 0 ? Math.max(2, r.users / baseUsers * 100) : 2;
            const rateText = i === 0
                ? "基准 100%"
                : "较上一级 " + Number(r.rate || 0).toFixed(1) + "%";
            return '<div class="db-funnel-row">'
                + '<div class="db-funnel-label">' + escapeHtml(r.label) + "</div>"
                + '<div class="db-funnel-track"><div class="db-funnel-fill" style="width:'
                + widthPct.toFixed(1) + '%"><span>' + (r.users || 0) + " 人</span></div></div>"
                + '<div class="db-funnel-rate">' + rateText + "</div>"
                + "</div>";
        }).join("");
    });
}

// ---------- 5. AI 经营简报：段落 + 分级 alerts ----------
function renderBriefing(res) {
    const box = document.getElementById("db-briefing");
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        box.innerHTML = dbFailBox("AI 简报加载失败");
        return;
    }
    const d = res.data.data || {};
    const paragraphs = (d.paragraphs || [])
        .map(p => '<p class="db-brief-p">' + escapeHtml(p) + "</p>").join("");
    const alerts = (d.alerts || []).map(a => {
        const cls = a.level === "good" ? "db-alert-good"
            : (a.level === "warn" ? "db-alert-warn" : "db-alert-info");
        const icon = a.level === "good" ? "✅" : (a.level === "warn" ? "⚠️" : "ℹ️");
        return '<div class="db-alert ' + cls + '">' + icon + " " + escapeHtml(a.text) + "</div>";
    }).join("");
    box.innerHTML = '<div class="db-brief-title">' + escapeHtml(d.title || "AI 经营简报") + "</div>"
        + paragraphs
        + (alerts || '<div class="db-empty">暂无预警与提示</div>');
}

// ---------- 6. AI 售后初审效果（全量）：CSS 进度条 + 建议分布 ----------
function loadAiPanel() {
    dbFetch("/api/admin/stats/ai/aftersale").then(res => {
        const box = document.getElementById("db-aipanel");
        if (res.status === 401 || res.status === 403) { dbAuthGuard(res.status); }
        if (!res.ok || res.status !== 200 || res.data.code !== 0) {
            box.innerHTML = dbFailBox("AI 初审面板加载失败");
            return;
        }
        const d = res.data.data || {};
        const dist = d.suggestion_dist || {};
        const rate = (d.agreement_rate === null || d.agreement_rate === undefined)
            ? null : Number(d.agreement_rate);
        const rateWidth = rate === null ? 0 : Math.min(100, Math.max(0, rate));
        const rateText = rate === null ? "暂无（无明确建议的已处理工单）" : rate.toFixed(1) + "%";
        box.innerHTML =
            '<div class="db-ai-topline">'
            + "<span>售后工单总数 <b>" + (d.total || 0) + "</b></span>"
            + "<span>AI 已分析 <b>" + (d.analyzed || 0) + "</b></span>"
            + "<span>待人工处理 <b>" + (d.pending || 0) + "</b></span>"
            + "<span>已处理 <b>" + (d.handled_total || 0) + "</b></span>"
            + "</div>"
            + '<div class="db-progress-text">建议与人工裁定一致率（manual 不进分母）：<b>'
            + rateText + "</b>，一致 " + (d.agreed || 0) + " / 可比 " + (d.decided_total || 0)
            + " 单</div>"
            + '<div class="db-progress"><div class="db-progress-fill" style="width:' + rateWidth.toFixed(1)
            + '%"></div></div>'
            + '<div class="db-sugg-nums">'
            + '<div class="db-sugg-num s-approve">建议同意<b>' + (dist.approve || 0) + "</b></div>"
            + '<div class="db-sugg-num s-reject">建议拒绝<b>' + (dist.reject || 0) + "</b></div>"
            + '<div class="db-sugg-num s-manual">人工核实<b>' + (dist.manual || 0) + "</b></div>"
            + "</div>";
    });
}


// ========== 看板二期：深度洞察（评价标签 / 库存预警 / RFM 分层） ==========
let p2ReviewLoaded = false;    // 评价标签是否已加载（首次随看板懒加载）
let rfmDays = 30;              // RFM 当前周期：7 / 30 / 90

// 二期各模块共用的 401/403 提示（复用一期顶部红条 db-auth-tip）
function p2AuthGuard(status) {
    if (status === 401 || status === 403) { dbAuthGuard(status); return true; }
    return false;
}

// ---------- 通用前端分页器 ----------
// 数据已全量在前端时（差评预警、RFM 用户明细），做客户端分页，无需改后端。
// state 形状：{items:[], page:1, perPage:5, renderItem:fn(item)->html, boxId, emptyText}
// 每次翻页只重渲染该盒子，内容多的模块不再一屏拉到底。
function clientPager(state) {
    const total = state.items.length;
    const totalPages = Math.max(1, Math.ceil(total / state.perPage));
    if (state.page > totalPages) state.page = totalPages;
    if (state.page < 1) state.page = 1;
    const startIdx = (state.page - 1) * state.perPage;
    const slice = state.items.slice(startIdx, startIdx + state.perPage);

    let body;
    if (!total) {
        body = '<div class="db-empty">' + (state.emptyText || "暂无数据") + "</div>";
    } else {
        body = (state.prefixHtml || "")
            + slice.map(state.renderItem).join("")
            + (state.suffixHtml || "");
    }

    // 底部分页条：上一页 / 页码（多页时）/ 下一页 + 总数
    let bar = "";
    if (totalPages > 1) {
        bar = '<div class="cp-bar">'
            + '<button type="button" class="cp-btn"'
            + (state.page === 1 ? ' disabled' : '')
            + ' onclick="pagerGo(\'' + state.key + "', -1)\">上一页</button>"
            + '<span class="cp-info">' + state.page + " / " + totalPages + "</span>"
            + '<button type="button" class="cp-btn"'
            + (state.page === totalPages ? ' disabled' : '')
            + ' onclick="pagerGo(\'' + state.key + "', 1)\">下一页</button>"
            + "</div>";
    }
    const countLine = '<div class="cp-count">共 ' + total + " 条，每页 "
        + state.perPage + " 条</div>";
    document.getElementById(state.boxId).innerHTML = body + bar + countLine;
}

// 所有分页器状态按 key 登记，翻页按钮通过 key 找回
const _PAGERS = {};
function registerPager(state) {
    _PAGERS[state.key] = state;
    clientPager(state);
}
function pagerGo(key, delta) {
    const state = _PAGERS[key];
    if (!state) return;
    state.page += delta;
    clientPager(state);
}

// ---------- 7. 评价标签洞察（标签横条 + summary） ----------
async function loadReviewInsights() {
    const tagBox = document.getElementById("p2-review");
    const badBox = document.getElementById("p2-badreviews");
    const res = await dbFetch("/api/admin/stats/review-insights?days=" + dashboardDays);
    p2ReviewLoaded = true;

    if (res.status === 401 || res.status === 403) { p2AuthGuard(res.status); }
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        tagBox.innerHTML = dbFailBox("评价标签加载失败");
        badBox.innerHTML = dbFailBox("差评预警加载失败");
        return;
    }
    const d = res.data.data || {};
    const sm = d.summary || {};
    const dist = d.sentiment_dist || {};
    const total = sm.total_reviews || 0;

    // summary 行：总数 / 带标签占比 / 情感分布
    tagBox.innerHTML =
        '<div class="p2-tag-head">'
        + "<span>评价总数 <b>" + total + "</b> 条 · 带标签 <b>"
        + (sm.tagged_reviews || 0) + '</b> 条（' + Number(d.tagged_rate || 0).toFixed(1)
        + '%）</span>'
        + "<span>👍 " + (dist.positive || 0) + " · 👎 " + (dist.negative || 0)
        + " · ➖ " + (dist.neutral || 0) + "</span></div>"
        + p2TagBlockHtml("正向标签", d.positive_tags || [], "pos")
        + p2TagBlockHtml("负向标签", d.negative_tags || [], "neg");

    // ---------- 差评预警：红底小列表（后端按评价时间倒序），分页查看 ----------
    const bad = d.bad_reviews || [];
    registerPager({
        key: "p2-bad",
        boxId: "p2-badreviews",
        items: bad,
        page: 1,
        perPage: 5,
        emptyText: "近 " + dashboardDays + " 天无差评（评分 ≤ 2）",
        renderItem: r =>
            '<div class="p2-bad-item">'
            + '<div class="p2-bad-top"><span>' + escapeHtml(r.username || "匿名用户")
            + ' · ' + (r.rating || 0) + ' 星 · 订单 #' + (r.order_id || "-") + '</span>'
            + '<span class="p2-bad-time">' + (r.created_at ? fmtTime(r.created_at) : "")
            + "</span></div>"
            + '<div class="p2-bad-body">' + escapeHtml(r.snippet || "（无评价内容）") + "</div>"
            + "</div>"
    });
}

// 一组标签横条：宽度按【本组】最高频次归一化（正/负各自归一化）
function p2TagBlockHtml(title, rows, cls) {
    if (!rows.length) {
        return '<div style="font-size:12.5px;color:#636e72;margin:10px 0 4px">'
            + title + "</div><div class=\"db-empty\">暂无命中</div>";
    }
    const maxCount = Math.max.apply(null, rows.map(r => Number(r.count) || 0)) || 1;
    let html = '<div style="font-size:12.5px;color:#636e72;margin:10px 0 6px">' + title + "</div>";
    html += rows.map(r => {
        const c = Number(r.count) || 0;
        const widthPct = (c / maxCount * 100).toFixed(1);
        return '<div class="p2-tag-row">'
            + '<div class="p2-tag-name">' + escapeHtml(r.tag) + "</div>"
            + '<div class="p2-tag-track"><div class="p2-tag-fill ' + cls
            + '" style="width:' + widthPct + '%"></div></div>'
            + '<div class="p2-tag-num">' + c + "</div></div>";
    }).join("");
    return html;
}

// ---------- 8. 库存预警：阈值校验提示 + 表格 ----------
async function loadInventoryAlerts() {
    const box = document.getElementById("p2-inventory");
    const input = document.getElementById("p2-inv-threshold");
    const text = String(input.value || "").trim();

    // 前端先拦一道明显非法（后端同样严格校验，双保险）
    if (!/^\d+$/.test(text)) {
        box.innerHTML = dbFailBox("阈值必须是 1-100 的整数");
        return;
    }
    const threshold = parseInt(text, 10);
    if (threshold < 1 || threshold > 100) {
        box.innerHTML = dbFailBox("阈值范围为 1-100");
        return;
    }

    box.innerHTML = '<div class="db-empty">加载中...</div>';
    const res = await dbFetch("/api/admin/stats/inventory-alerts?threshold=" + threshold);
    if (res.status === 401 || res.status === 403) { p2AuthGuard(res.status); }
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        box.innerHTML = dbFailBox(res.data.msg || "库存预警加载失败");
        return;
    }
    const d = res.data.data || {};
    const items = d.items || [];
    if (!items.length) {
        box.innerHTML = '<div class="db-empty">阈值 ' + threshold + " 内没有需要预警的在售商品</div>";
        return;
    }
    box.innerHTML =
        '<table class="admin-table"><thead><tr><th>ID</th><th>商品</th><th>库存</th>'
        + "<th>价格</th><th>状态</th></tr></thead><tbody>"
        + items.map(r => {
            const isZero = r.stock === 0;
            const stockCls = isZero ? "p2-stock-0" : "p2-stock-low";
            const badge = isZero
                ? '<span class="p2-badge b0">缺货</span>'
                : '<span class="p2-badge bl">低货</span>';
            return "<tr><td>" + r.id + "</td><td>" + escapeHtml(r.name) + "</td>"
                + '<td class="' + stockCls + '">' + r.stock + "</td>"
                + "<td>" + dbMoney(r.price) + "</td><td>" + badge + "</td></tr>";
        }).join("")
        + "</tbody></table>"
        + '<div style="font-size:12px;color:#b2bec3;margin-top:8px">共 '
        + (d.total || 0) + " 条，当前阈值 " + (d.threshold || threshold) + "</div>";
}

// ---------- 9. 用户 RFM 分层：周期切换 + 8 层卡片 ----------
function switchRfmDays(days, btn) {
    if (days === rfmDays) return;
    rfmDays = days;
    document.querySelectorAll('[data-rfmdays]').forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    loadRfm();
}

async function loadRfm() {
    const box = document.getElementById("p2-rfm");
    const medianBox = document.getElementById("p2-rfm-median");
    box.innerHTML = '<div class="db-empty">加载中...</div>';

    const res = await dbFetch("/api/admin/stats/rfm?days=" + rfmDays);
    if (res.status === 401 || res.status === 403) { p2AuthGuard(res.status); }
    if (!res.ok || res.status !== 200 || res.data.code !== 0) {
        box.innerHTML = dbFailBox("RFM 分层加载失败");
        return;
    }
    const d = res.data.data || {};
    const segments = d.segments || {};
    const median = d.median || {};
    const summary = d.summary || {};

    medianBox.innerHTML = "打分口径：各维度与全体中位数比较——R(最近购买距今天数) ≤ 中位数记 2，"
        + "F(订单数) / M(消费额) ≥ 中位数记 2；按 RFM 分值映射 8 类经典分层。<br>"
        + "当前窗口中位数：R = <b>" + Number(median.recency || 0).toFixed(1)
        + "</b> 天 · F = <b>" + Number(median.frequency || 0).toFixed(1)
        + "</b> 单 · M = <b>" + dbMoney(median.monetary || 0) + "</b>"
        + "；窗口内有效购买用户 <b>" + (summary.total_users || 0) + "</b> 人。";

    if (!(summary.total_users || 0)) {
        box.innerHTML = '<div class="db-empty">近 ' + rfmDays + " 天没有有效订单，暂无可分层用户</div>";
        return;
    }

    // 固定 8 层顺序（重要→一般、价值→挽留）
    const order = ["222", "212", "122", "112", "221", "211", "121", "111"];
    box.innerHTML = '<div class="p2-rfm-grid">' + order.map(code => {
        const seg = segments[code] || { label: code, count: 0, users: [] };
        const emptyCls = seg.count ? "" : " p2-rfm-zero";
        const toggle = seg.count
            ? '<span class="p2-rfm-toggle" onclick="toggleRfmUsers(\'' + code
              + "', this)\">查看 " + seg.count + " 人明细 ▾</span>"
            : '<span style="font-size:12px;color:#b2bec3">无用户</span>';
        return '<div class="p2-rfm-card' + emptyCls + '">'
            + '<div class="p2-rfm-head"><span>' + escapeHtml(seg.label)
            + '</span><span class="p2-rfm-code">RFM ' + code + "</span></div>"
            + '<div class="p2-rfm-count"><b>' + seg.count + "</b> 人</div>"
            + toggle
            // 用户明细容器：内容由分页器填入（超过每页条数自动出现翻页）
            + '<div class="p2-rfm-users" id="p2-rfm-users-' + code + '"></div>'
            + "</div>";
    }).join("") + "</div>";

    // 为每层有用户的卡片登记一个独立分页器（每页 5 人）
    order.forEach(code => {
        const seg = segments[code];
        if (seg && (seg.users || []).length) {
            registerPager({
                key: "rfm-" + code,
                boxId: "p2-rfm-users-" + code,
                items: seg.users,
                page: 1,
                perPage: 5,
                emptyText: "无",
                prefixHtml: '<table class="p2-rfm-table"><thead><tr>'
                    + "<th>UID</th><th>用户名</th><th>R(天)</th><th>F(单)</th>"
                    + "<th>M(元)</th><th>RFM</th></tr></thead><tbody>",
                suffixHtml: "</tbody></table>",
                renderItem: u =>
                    "<tr><td>" + (u.user_id || "-") + "</td><td>"
                    + escapeHtml(u.username || "-") + "</td><td>"
                    + (u.r_days || 0) + "</td><td>" + (u.f || 0)
                    + "</td><td>" + Number(u.m || 0).toFixed(2)
                    + "</td><td>" + escapeHtml(u.rfm || "") + "</td></tr>"
            });
        }
    });
}

// 某层卡片的用户明细表（简单 table；展开/收起纯样式切换）
function toggleRfmUsers(code, btn) {
    const box = document.getElementById("p2-rfm-users-" + code);
    if (!box) return;
    const openNow = box.style.display === "block";
    box.style.display = openNow ? "none" : "block";
    btn.textContent = openNow ? "查看 " + btn.textContent.match(/\d+/)[0] + " 人明细 ▾"
        : "收起明细 ▴";
}

// ---------- 初始化 ----------
renderFilterBar();
renderAftersaleFilterBar();
loadAdminOrders();
loadAdminProducts();
loadAdminAftersales();
