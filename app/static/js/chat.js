// ========== AI 智能客服（全站右下角悬浮窗） ==========
// 后端接口：POST /api/ai/chat，游客也能聊；查订单等个人问题由后端校验登录
(function () {
    const QUICK_QUESTIONS = ["我的订单到哪了？", "键盘还有货吗？", "推荐点东西", "怎么退货？"];

    // 注入浮窗样式（只在浏览器里生效，不依赖 css 文件）
    const style = document.createElement("style");
    style.textContent = `
        .ai-fab {
            position: fixed; right: 28px; bottom: 28px; width: 58px; height: 58px;
            border-radius: 50%; background: linear-gradient(135deg, #0984e3, #6c5ce7);
            color: #fff; border: none; cursor: pointer; font-size: 26px;
            box-shadow: 0 6px 20px rgba(9,132,227,.4); z-index: 999;
        }
        .ai-fab:hover { transform: scale(1.06); }
        .ai-panel {
            position: fixed; right: 28px; bottom: 98px; width: 370px; max-width: calc(100vw - 40px);
            height: 520px; max-height: calc(100vh - 140px); background: #fff; border-radius: 14px;
            box-shadow: 0 10px 40px rgba(0,0,0,.18); z-index: 1000; display: none;
            flex-direction: column; overflow: hidden;
        }
        .ai-panel.open { display: flex; }
        .ai-head {
            background: linear-gradient(135deg, #0984e3, #6c5ce7); color: #fff;
            padding: 14px 16px; font-weight: 700; font-size: 15px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .ai-head .ai-close { background: none; border: none; color: #fff; font-size: 20px; cursor: pointer; }
        .ai-msgs { flex: 1; overflow-y: auto; padding: 14px; background: #f6f8fb; }
        .ai-msg { display: flex; margin-bottom: 12px; }
        .ai-msg .ai-avatar { width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center; font-size: 17px; margin-right: 8px; }
        .ai-msg.bot .ai-avatar { background: #e3f2fd; }
        .ai-msg.me { flex-direction: row-reverse; }
        .ai-msg.me .ai-avatar { background: #00b894; margin-right: 0; margin-left: 8px; }
        .ai-bubble {
            max-width: 75%; padding: 9px 13px; border-radius: 12px; font-size: 14px;
            line-height: 1.55; white-space: pre-wrap; word-break: break-word;
        }
        .ai-msg.bot .ai-bubble { background: #fff; border: 1px solid #eef0f4; border-top-left-radius: 4px; }
        .ai-msg.me .ai-bubble { background: #0984e3; color: #fff; border-top-right-radius: 4px; }
        .ai-card {
            display: flex; gap: 8px; align-items: center; background: #fff;
            border: 1px solid #e3f2fd; border-radius: 10px; padding: 8px 10px; margin: 6px 0;
            cursor: pointer; text-decoration: none; color: inherit;
        }
        .ai-card:hover { background: #f0f7ff; }
        .ai-card img { width: 40px; height: 40px; border-radius: 8px; object-fit: cover; }
        .ai-card .ai-card-name { font-size: 14px; font-weight: 600; }
        .ai-card .ai-card-price { font-size: 13px; color: #ff4757; font-weight: 700; }
        .ai-quick { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 14px; background: #fff; border-top: 1px solid #f1f2f6; }
        .ai-quick button {
            border: 1px solid #dfe6e9; background: #f8f9fb; border-radius: 16px; padding: 5px 12px;
            font-size: 12px; cursor: pointer; color: #636e72;
        }
        .ai-quick button:hover { border-color: #0984e3; color: #0984e3; }
        .ai-input { display: flex; gap: 8px; padding: 10px 14px; border-top: 1px solid #f1f2f6; background: #fff; }
        .ai-input input {
            flex: 1; border: 1px solid #dfe6e9; border-radius: 20px; padding: 9px 14px; font-size: 14px; outline: none;
        }
        .ai-input input:focus { border-color: #0984e3; }
        .ai-input button {
            border: none; background: #0984e3; color: #fff; border-radius: 20px;
            padding: 0 18px; font-size: 14px; cursor: pointer; font-weight: 600;
        }
    `;
    document.head.appendChild(style);

    // 挂载浮窗按钮 + 聊天面板
    const fab = document.createElement("button");
    fab.className = "ai-fab";
    fab.innerHTML = "💬";
    fab.title = "智能客服";
    document.body.appendChild(fab);

    const panel = document.createElement("div");
    panel.className = "ai-panel";
    panel.innerHTML = `
        <div class="ai-head">
            <span>🤖 智能客服小极</span>
            <button class="ai-close" onclick="this.closest('.ai-panel').classList.remove('open')">×</button>
        </div>
        <div class="ai-msgs" id="ai-msgs"></div>
        <div class="ai-quick">
            ${QUICK_QUESTIONS.map(q => `<button onclick="aiSend('${q}')">${q}</button>`).join("")}
        </div>
        <div class="ai-input">
            <input id="ai-input" placeholder="问我商品、订单、推荐……"
                   onkeydown="if(event.key==='Enter')aiSend()">
            <button onclick="aiSend()">发送</button>
        </div>
    `;
    document.body.appendChild(panel);

    const msgsBox = () => document.getElementById("ai-msgs");
    let welcomed = false;

    function scrollBottom() {
        const box = msgsBox();
        box.scrollTop = box.scrollHeight;
    }

    // 防 XSS：用 textContent 渲染用户输入和文本回复
    function addMsg(role, text, products) {
        const row = document.createElement("div");
        row.className = "ai-msg " + role;
        const avatar = document.createElement("div");
        avatar.className = "ai-avatar";
        avatar.textContent = role === "bot" ? "🤖" : "🙂";
        const bubble = document.createElement("div");
        bubble.className = "ai-bubble";
        bubble.textContent = text;
        row.appendChild(avatar);
        row.appendChild(bubble);

        // 商品卡片（推荐/查商品时返回）
        if (products && products.length) {
            const wrap = document.createElement("div");
            wrap.style.width = "100%";
            products.forEach(p => {
                const a = document.createElement("a");
                a.className = "ai-card";
                a.href = "/";
                const img = document.createElement("img");
                // 多图商品 image 字段是逗号分隔串：优先用 images 数组首图，无数组时取逗号前第一段
                img.src = `/static/images/${(p.images && p.images.length) ? p.images[0] : String(p.image || "default.svg").split(",")[0].trim() || "default.svg"}`;
                img.onerror = () => {
            if (img.dataset.fb) { img.style.display = "none"; }
            else { img.dataset.fb = "1"; img.src = "/static/images/default.svg"; }
        };
                const info = document.createElement("div");
                const name = document.createElement("div");
                name.className = "ai-card-name";
                name.textContent = p.name;
                const price = document.createElement("div");
                price.className = "ai-card-price";
                price.textContent = `¥${Number(p.price).toFixed(2)}`;
                info.appendChild(name);
                info.appendChild(price);
                a.appendChild(img);
                a.appendChild(info);
                wrap.appendChild(a);
            });
            bubble.appendChild(wrap);
        }
        msgsBox().appendChild(row);
        scrollBottom();
    }

    async function aiSend(prefill) {
        const input = document.getElementById("ai-input");
        const message = (prefill || input.value || "").trim();
        if (!message) return;
        if (!prefill) input.value = "";
        addMsg("me", message);

        try {
            const { status, data } = await api("/api/ai/chat", {
                method: "POST",
                body: { message }
            });
            if (data.code === 0) {
                addMsg("bot", data.data.reply, data.data.products);
                if (data.data.need_login) {
                    setTimeout(() => { location.href = "/login"; }, 1500);
                }
            } else {
                addMsg("bot", data.msg || "服务开小差了，稍后再试~");
            }
        } catch (e) {
            addMsg("bot", "网络好像不太好，稍后再试试~");
        }
    }

    window.aiSend = aiSend;

    fab.addEventListener("click", () => {
        panel.classList.toggle("open");
        if (panel.classList.contains("open") && !welcomed) {
            welcomed = true;
            setTimeout(() => addMsg("bot",
                "你好呀！我是智能客服小极 🤖\n可以问我商品库存、订单状态、让我推荐好物，退货政策也能答~ 点下面的快捷问题试试吧！"), 200);
        }
        if (panel.classList.contains("open")) scrollBottom();
    });
})();
