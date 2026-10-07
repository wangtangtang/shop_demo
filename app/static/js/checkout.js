// ========== 收银台：选择支付方式 → 扫码 → 模拟支付回调 ==========
const ORDER_ID = parseInt(location.pathname.split("/").pop(), 10);
let pollTimer = null;

async function bootstrap() {
    if (!getToken()) {
        location.href = "/login";
        return;
    }

    // 查订单当前状态（含金额）
    const { data } = await api(`/api/orders/${ORDER_ID}/pay-status`);
    if (data.code !== 0) {
        document.getElementById("pay-content").innerHTML =
            `<p style="color:#ff4757">${data.msg || "订单不存在"}</p>
             <button class="btn-primary" style="margin-top:16px" onclick="location.href='/orders'">返回订单列表</button>`;
        return;
    }

    const order = data.data;
    if (["paid", "shipped", "completed"].includes(order.status)) {
        showSuccess(order);
    } else if (order.status === "cancelled") {
        document.getElementById("pay-content").innerHTML =
            `<p style="color:#636e72;font-size:16px">订单已取消</p>
             <button class="btn-primary" style="margin-top:16px" onclick="location.href='/orders'">返回订单列表</button>`;
    } else if (order.trade_no && order.pay_closed !== 1) {
        // 待支付且已选过渠道、支付单仍有效（刷新页面场景）：直接回扫码页
        // 同时把金额带上（pay-status 有 total_amount），扫码页要显示应付金额
        // pay_closed=1（用户返回重选时已关闭旧支付单）→ 走 else 回渠道选择，
        // 修复"返回重选后从订单页再进又回到旧渠道扫码页"
        showQrcode(order);
    } else {
        // 待支付：先选支付方式
        stopPolling();  // 兜底：回到选渠道视图时确保没有轮询在跑
        showChannelSelect(order);
    }
}

// 停止支付状态轮询（返回重选渠道 / 离开扫码页时调用）
function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

// 「返回重选支付方式」：停止轮询，先通知后端关闭当前支付单（旧二维码/迟到回调
// 会被后端拒绝），再回到渠道选择视图。订单保持 pending 不动，重新选渠道会重新
// POST /orders/<id>/pay：后端生成新 trade_no 并把 pay_closed 归 0（新支付单生效）。
// 关闭接口失败也允许本地返回（toast 提示），避免网络异常把用户卡死在扫码页。
async function backToChannelSelect() {
    stopPolling();
    try {
        const res = await api(`/api/orders/${ORDER_ID}/pay/cancel`, { method: "POST" });
        if (res.data.code !== 0) {
            toast(res.data.msg || "支付单关闭失败", "error");
        }
    } catch (e) {
        toast("支付单关闭失败，可重新选择支付方式", "error");
    }
    const { data } = await api(`/api/orders/${ORDER_ID}/pay-status`);
    if (data.code !== 0) {
        toast(data.msg || "订单不存在", "error");
        return;
    }
    const order = data.data;
    if (order.status !== "pending") {
        // 轮询间隙恰好支付成功：直接展示成功页
        showSuccess(order);
        return;
    }
    showChannelSelect(order);
}

function showChannelSelect(order) {
    document.getElementById("pay-content").innerHTML = `
        <h1 style="margin-bottom:4px">收银台</h1>
        <p class="auth-subtitle">订单号 #${ORDER_ID} · 应付 <b style="color:#ff4757;font-size:20px">¥${(order.total_amount || 0).toFixed(2)}</b></p>
        <div style="margin:20px 0">
            <div onclick="chooseChannel('wechat')" style="border:2px solid #eee;border-radius:10px;padding:16px;margin:10px 0;cursor:pointer;display:flex;align-items:center;gap:12px;justify-content:center"
                 onmouseover="this.style.borderColor='#07c160'" onmouseout="this.style.borderColor='#eee'">
                <span style="font-size:28px">💚</span>
                <b style="font-size:17px">微信支付</b>
            </div>
            <div onclick="chooseChannel('alipay')" style="border:2px solid #eee;border-radius:10px;padding:16px;margin:10px 0;cursor:pointer;display:flex;align-items:center;gap:12px;justify-content:center"
                 onmouseover="this.style.borderColor='#1677ff'" onmouseout="this.style.borderColor='#eee'">
                <span style="font-size:28px">💙</span>
                <b style="font-size:17px">支付宝</b>
            </div>
        </div>
        <p style="color:#b2bec3;font-size:13px">模拟支付环境：不会发生真实扣款</p>
    `;
}

async function chooseChannel(channel) {
    const { data } = await api(`/api/orders/${ORDER_ID}/pay`, {
        method: "POST",
        body: { channel }
    });
    if (data.code !== 0) {
        toast(data.msg || "发起支付失败", "error");
        return;
    }
    showQrcode({
        pay_channel: data.data.channel,
        trade_no: data.data.trade_no,
        total_amount: data.data.amount
    });
}

function showQrcode(order) {
    const channelName = order.pay_channel === "alipay" ? "支付宝" : "微信支付";
    const themeColor = order.pay_channel === "alipay" ? "#1677ff" : "#07c160";
    document.getElementById("pay-content").innerHTML = `
        <h1 style="margin-bottom:4px">${channelName}扫码支付</h1>
        <p class="auth-subtitle">模拟环境，无需真扫</p>
        <div style="margin:18px 0">
            ${fakeQrcode()}
        </div>
        <p>支付金额：<b style="color:#ff4757;font-size:22px">¥${(order.total_amount || 0).toFixed(2)}</b></p>
        <p style="color:#636e72;font-size:13px;margin:6px 0">流水号：${order.trade_no || ""}</p>
        <button id="mock-pay-btn" class="btn-primary btn-block" style="margin-top:14px;background:${themeColor}">
            模拟${channelName}付款成功
        </button>
        <button class="btn-secondary btn-block" style="margin-top:10px" onclick="backToChannelSelect()">
            返回重选支付方式
        </button>
        <p style="color:#b2bec3;font-size:12px;margin-top:12px;line-height:1.7">
            真实场景：顾客扫码付款后，支付平台【异步回调】通知后端改订单状态<br>
            此按钮即模拟该回调
        </p>
    `;

    document.getElementById("mock-pay-btn").onclick = mockPay;
    // 兜底：每 1.5 秒轮询一次支付状态（真实系统常见做法：回调 + 前端轮询双通道）
    stopPolling();
    pollTimer = setInterval(checkPaid, 1500);
}

async function mockPay() {
    const btn = document.getElementById("mock-pay-btn");
    btn.disabled = true;
    btn.textContent = "支付处理中…";

    // 模拟支付平台 POST 商家的 notify_url（异步回调）
    const { data } = await api("/api/pay/mock-notify", {
        method: "POST",
        body: { order_id: ORDER_ID }
    });
    if (data.code !== 0) {
        toast(data.msg || "回调失败", "error");
        btn.disabled = false;
        btn.textContent = "重新付款";
        return;
    }
    checkPaid();
}

async function checkPaid() {
    const { data } = await api(`/api/orders/${ORDER_ID}/pay-status`);
    if (data.code !== 0) return;
    const order = data.data;
    if (["paid", "shipped", "completed"].includes(order.status)) {
        stopPolling();
        showSuccess(order);
        return;
    }
    // 极端场景：另一个标签页/设备关闭了当前支付单，本页停止轮询并回渠道选择，
    // 避免用户继续对着已作废的二维码付款
    if (order.status === "pending" && order.pay_closed === 1) {
        stopPolling();
        toast("支付单已关闭，请重新选择支付方式");
        showChannelSelect(order);
    }
}

function showSuccess(order) {
    const channelName = order.pay_channel === "alipay" ? "支付宝" : (order.pay_channel === "wechat" ? "微信支付" : "");
    document.getElementById("pay-content").innerHTML = `
        <div style="font-size:52px">✅</div>
        <h1 style="margin:10px 0 4px">支付成功</h1>
        <p class="auth-subtitle">${channelName ? channelName + " · " : ""}商家正在准备发货</p>
        <div style="margin:22px 0 6px;display:flex;gap:12px;justify-content:center">
            <button class="btn-primary" onclick="location.href='/orders'">查看订单</button>
            <button class="btn-secondary" onclick="location.href='/'">继续购物</button>
        </div>
    `;
}

// 纯前端 CSS 伪二维码（随机黑白块），仅用于演示，不编码真实内容
function fakeQrcode() {
    const size = 21;
    let html = `<div style="display:inline-grid;grid-template-columns:repeat(${size},8px);grid-template-rows:repeat(${size},8px);border:8px solid #fff;box-shadow:0 0 0 1px #dfe6e9;border-radius:4px">`;
    for (let i = 0; i < size * size; i++) {
        html += `<div style="width:8px;height:8px;background:${Math.random() > 0.5 ? "#111" : "#fff"}"></div>`;
    }
    return html + "</div>";
}

bootstrap();
