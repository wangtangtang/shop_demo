// ========== 收货地址簿 ==========
let editingId = null;   // 正在编辑的地址 id；null 表示新增模式
let addrCache = [];     // 当前列表数据（编辑回填用）

async function loadAddresses() {
    if (!getToken()) {
        location.href = "/login";
        return;
    }
    const { status, data } = await api("/api/addresses");
    const list = document.getElementById("addr-list");
    if (status === 401) {
        location.href = "/login";
        return;
    }
    if (data.code !== 0) {
        list.innerHTML = `<div class="empty-state">加载失败</div>`;
        return;
    }
    addrCache = data.data;
    renderList(addrCache, list);
}

function renderList(addrs, list) {
    if (!addrs.length) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📍</div>
                <p>还没有收货地址，先添加一个吧</p>
            </div>`;
        return;
    }
    // 后端已按「默认优先 + 创建时间倒序」排好序
    list.innerHTML = addrs.map(a => `
        <div class="addr-card ${a.is_default ? "default" : ""}">
            <div class="addr-info">
                <span class="addr-name">${escapeHtml(a.receiver_name)}</span>
                <span>${escapeHtml(a.receiver_phone)}</span>
                ${a.is_default ? '<span class="addr-tag">默认</span>' : ""}
                <div>
                    <span class="addr-region">${escapeHtml(a.region)}</span>
                    ${escapeHtml(a.detail)}
                </div>
            </div>
            <div class="addr-actions">
                ${a.is_default ? "" : `<button class="addr-link-btn primary" onclick="setDefault(${a.id})">设为默认</button>`}
                <button class="addr-link-btn" onclick="editAddress(${a.id})">编辑</button>
                <button class="addr-link-btn red" onclick="removeAddress(${a.id})">删除</button>
            </div>
        </div>
    `).join("");
}

function getFormPayload() {
    return {
        receiver_name: document.getElementById("a-name").value.trim(),
        receiver_phone: document.getElementById("a-phone").value.trim(),
        region: document.getElementById("a-region").value.trim(),
        detail: document.getElementById("a-detail").value.trim(),
        is_default: document.getElementById("a-default").checked
    };
}

async function saveAddress() {
    const body = getFormPayload();
    if (!body.receiver_name) { toast("请填写收货人姓名", "error"); return; }
    if (!/^1[3-9]\d{9}$/.test(body.receiver_phone)) {
        toast("手机号格式不正确（请输入11位大陆手机号）", "error"); return;
    }
    if (!body.region) { toast("请填写所在地区", "error"); return; }
    if (!body.detail) { toast("请填写详细地址", "error"); return; }

    const url = editingId ? `/api/addresses/${editingId}` : "/api/addresses";
    const { data } = await api(url, {
        method: editingId ? "PUT" : "POST",
        body
    });
    if (data.code === 0) {
        toast(editingId ? "地址已更新" : "地址已添加", "success");
        resetForm();
        loadAddresses();
    } else {
        toast(data.msg || "保存失败", "error");
    }
}

function editAddress(id) {
    const a = addrCache.find(x => x.id === id);
    if (!a) return;
    editingId = id;
    document.getElementById("addr-form-title").textContent = "✏️ 编辑收货地址";
    document.getElementById("a-name").value = a.receiver_name;
    document.getElementById("a-phone").value = a.receiver_phone;
    document.getElementById("a-region").value = a.region;
    document.getElementById("a-detail").value = a.detail;
    document.getElementById("a-default").checked = a.is_default === 1;
    document.getElementById("addr-cancel-btn").style.display = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
}

async function removeAddress(id) {
    if (!confirm("确定删除这个收货地址吗？")) return;
    const { data } = await api(`/api/addresses/${id}`, { method: "DELETE" });
    if (data.code === 0) {
        toast("地址已删除", "success");
        // 如果删的是正在编辑的地址，表单复位为新增模式
        if (editingId === id) resetForm();
        loadAddresses();
    } else {
        toast(data.msg || "删除失败", "error");
    }
}

async function setDefault(id) {
    const { data } = await api(`/api/addresses/${id}/set-default`, { method: "POST" });
    if (data.code === 0) {
        toast("已设为默认地址", "success");
        loadAddresses();
    } else {
        toast(data.msg || "操作失败", "error");
    }
}

function resetForm() {
    editingId = null;
    document.getElementById("addr-form-title").textContent = "➕ 新增收货地址";
    ["a-name", "a-phone", "a-region", "a-detail"].forEach(i =>
        document.getElementById(i).value = "");
    document.getElementById("a-default").checked = false;
    document.getElementById("addr-cancel-btn").style.display = "none";
}

loadAddresses();
