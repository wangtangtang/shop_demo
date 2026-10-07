// ========== 购物车 ==========
const PRODUCT_ICONS = ["⌨️", "🖱️", "🖥️", "🔌", "🎧"];
let selectedAddressId = null;  // 结算时选中的收货地址 id
// 接口返回的全部购物车条目（唯一数据源，搜索筛选绝不能改写它）
let cartAllItems = [];
// 当前搜索关键字（纯前端即时筛选，只控制显示/隐藏，不清空勾选状态）
let cartKeyword = "";
// 当前已勾选的购物车条目 id 集合（页面级唯一勾选数据源，独立于搜索过滤：
// 过滤只改变可见条目，勾选不丢失，清空搜索后仍在）
let selectedIds = new Set();

async function loadAddresses() {
    // 加载收货地址并渲染成单选卡片（默认地址预选）；无地址给出引导
    const listBox = document.getElementById("cart-addr-list");
    const emptyBox = document.getElementById("cart-addr-empty");
    if (!listBox) return;
    const { data } = await api("/api/addresses");
    if (data.code !== 0) return;
    const addrs = data.data;
    if (!addrs.length) {
        listBox.innerHTML = "";
        emptyBox.style.display = "block";
        selectedAddressId = null;
        return;
    }
    emptyBox.style.display = "none";
    // 默认选中默认地址；没有默认标记则选第一条（后端已把默认排第一）
    selectedAddressId = (addrs.find(a => a.is_default) || addrs[0]).id;
    listBox.innerHTML = addrs.map(a => `
        <div class="cart-addr-card ${a.id === selectedAddressId ? "selected" : ""}"
             data-aid="${a.id}" onclick="selectAddress(${a.id})">
            <span class="cart-addr-radio"></span>
            <span class="cart-addr-text">
                <span class="cart-addr-name">${escapeHtml(a.receiver_name)}</span>${escapeHtml(a.receiver_phone)}
                ${a.is_default ? '<span class="cart-addr-tag">默认</span>' : ""}<br>
                ${escapeHtml(a.region)} ${escapeHtml(a.detail)}
            </span>
        </div>
    `).join("");
}

function selectAddress(id) {
    selectedAddressId = id;
    document.querySelectorAll(".cart-addr-card").forEach(card => {
        card.classList.toggle("selected", card.dataset.aid == id);
    });
}

async function loadCart() {
    if (!getToken()) {
        document.getElementById("cart-login-tip").style.display = "block";
        return;
    }

    const { data } = await api("/api/cart");
    if (data.code !== 0) {
        toast(data.msg || "加载失败", "error");
        return;
    }

    const items = data.data.items;
    const empty = document.getElementById("cart-empty");
    const content = document.getElementById("cart-content");

    if (!items.length) {
        empty.style.display = "block";
        content.style.display = "none";
        return;
    }
    // 有商品时确保全选条可见（过滤空结果时 renderCartItems 不负责隐藏它）
    const selectBar0 = document.getElementById("cart-select-bar");
    if (selectBar0) selectBar0.style.display = "flex";

    empty.style.display = "none";
    content.style.display = "block";
    // 购物车有商品时才加载地址选择（地址区在结算区内）
    loadAddresses();

    // 保存完整数据后交给渲染函数（搜索框输入只重新渲染，不重新请求接口）。
    // 清掉已经不在购物车里的勾选 id（条目可能在别的页面被删/被下单清掉）
    cartAllItems = items;
    const aliveIds = new Set(items.map(it => it.id));
    selectedIds = new Set([...selectedIds].filter(id => aliveIds.has(id)));
    renderCartItems();
}

// 按当前搜索关键字过滤（不改动 cartAllItems 原始数据）
function visibleCartItems() {
    if (!cartKeyword) return cartAllItems;
    return cartAllItems.filter(it =>
        String(it.product_name || "").toLowerCase().indexOf(cartKeyword.toLowerCase()) !== -1);
}

// 渲染购物车条目（输入搜索关键字/勾选变化时被反复调用）
function renderCartItems() {
    const list = document.getElementById("cart-list");
    const matchEmpty = document.getElementById("cart-empty-match");
    const selectBar = document.getElementById("cart-select-bar");
    const items = visibleCartItems();
    if (!items.length) {
        list.innerHTML = "";
        matchEmpty.style.display = "block";
        // 过滤结果为空时全选条仍保留（可继续改搜索词），全选框恢复未选中/无半选
        const all = document.getElementById("cart-check-all");
        if (all) { all.checked = false; all.indeterminate = false; }
        updateSelectedSummary();
        return;
    }
    matchEmpty.style.display = "none";
    if (selectBar) selectBar.style.display = "flex";
    list.innerHTML = items.map((it) => {
        const icon = PRODUCT_ICONS[(it.product_id - 1) % PRODUCT_ICONS.length];
        const checked = selectedIds.has(it.id) ? "checked" : "";
        return `
            <div class="cart-item">
                <input type="checkbox" class="cart-check" value="${it.id}" ${checked}
                       onchange="toggleItem(${it.id}, this.checked)" title="勾选结算">
                <div class="cart-item-img">
                    <img src="/static/images/${it.image || 'default.svg'}"
                         onerror="if(this.dataset.fb){this.parentElement.innerHTML='${icon}'}else{this.dataset.fb=1;this.src='/static/images/default.svg'}">
                </div>
                <div>
                    <div class="cart-item-name">${it.product_name}</div>
                    <div class="cart-item-price">¥${it.price.toFixed(2)}</div>
                </div>
                <div class="qty-control">
                    <button onclick="changeQty(${it.id}, ${it.quantity - 1})">−</button>
                    <span>${it.quantity}</span>
                    <button onclick="changeQty(${it.id}, ${it.quantity + 1})">+</button>
                </div>
                <div class="cart-item-subtotal">¥${it.subtotal.toFixed(2)}</div>
                <button class="btn-delete" onclick="removeItem(${it.id})" title="删除">🗑️</button>
            </div>
        `;
    }).join("");
    syncCheckAllBox();
    updateSelectedSummary();
}

// 底部结算区：实时显示已选件数与勾选商品合计（未勾选不计入）
function updateSelectedSummary() {
    const countEl = document.getElementById("cart-selected-count");
    const totalEl = document.getElementById("cart-total");
    if (!countEl || !totalEl) return;
    let count = 0;
    let total = 0;
    cartAllItems.forEach(it => {
        if (selectedIds.has(it.id)) {
            count += it.quantity;
            total += it.subtotal;
        }
    });
    countEl.textContent = count;
    totalEl.textContent = "¥" + total.toFixed(2);
}

// 单条勾选/取消
function toggleItem(itemId, checked) {
    if (checked) selectedIds.add(itemId);
    else selectedIds.delete(itemId);
    syncCheckAllBox();
    updateSelectedSummary();
}

// 全选框状态（只看当前【可见】条目）：
//   可见项全部勾选 → 打勾；勾选了一部分 → 半选(indeterminate 横杠)；都没勾 → 空
// 半选态让用户在搜索过滤时也能看出"当前列表只勾了一部分"
function syncCheckAllBox() {
    const box = document.getElementById("cart-check-all");
    if (!box) return;
    const vis = visibleCartItems();
    const visChecked = vis.filter(it => selectedIds.has(it.id)).length;
    box.checked = vis.length > 0 && visChecked === vis.length;
    box.indeterminate = visChecked > 0 && visChecked < vis.length;
}

// 勾选全选：只勾选当前可见条目（无搜索=全部；搜索后=过滤结果，不勾看不见的）；
// 取消全选：清空【全部】勾选（含被搜索过滤隐藏的条目）——
// 否则会出现"界面上一个勾都没有，底部却在结算隐藏商品"的歧义
function toggleSelectAll(checked) {
    if (checked) {
        visibleCartItems().forEach(it => selectedIds.add(it.id));
    } else {
        selectedIds.clear();
    }
    renderCartItems();
}

// 搜索框 oninput：纯前端即时筛选，不发请求、不清空勾选
function filterCartItems() {
    cartKeyword = document.getElementById("cart-keyword").value.trim();
    renderCartItems();
}

async function changeQty(itemId, newQty) {
    if (newQty <= 0) {
        await removeItem(itemId);
        return;
    }
    await api(`/api/cart/${itemId}`, {
        method: "PUT",
        body: { quantity: newQty }
    });
    loadCart();
    updateCartBadge();
}

async function removeItem(itemId) {
    await api(`/api/cart/${itemId}`, { method: "DELETE" });
    selectedIds.delete(itemId);
    toast("已删除", "success");
    loadCart();
    updateCartBadge();
}

async function checkout() {
    if (selectedIds.size === 0) {
        toast("请先勾选要购买的商品", "error");
        return;
    }
    if (!selectedAddressId) {
        toast("请先选择收货地址（没有地址请先添加）", "error");
        return;
    }
    const { status, data } = await api("/api/orders", {
        method: "POST",
        body: {
            address_id: selectedAddressId,
            cart_item_ids: [...selectedIds]  // 只结算勾选条目
        }
    });
    if (status === 200 && data.code === 0) {
        selectedIds.clear();  // 结算成功清理本地勾选
        toast(`下单成功！订单号 #${data.data.order_id}`, "success");
        updateCartBadge();
        setTimeout(() => location.href = "/orders", 1000);
    } else {
        toast(data.msg || "下单失败", "error");
        // 可能库存/下架状态已变化，刷新列表与合计（勾选保留，由 loadCart 剔除失效 id）
        loadCart();
    }
}

loadCart();
