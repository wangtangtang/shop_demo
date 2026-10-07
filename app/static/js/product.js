// ========== 商品详情页：多图轮播 + 商品信息 + 加购 ==========
const PRODUCT_ID = parseInt(location.pathname.split("/").pop(), 10);

let galleryImages = [];   // 当前商品的图片数组（后端 images 字段）
let galleryIndex = 0;     // 轮播当前下标
let galleryTimer = null;  // 自动播放定时器

// 商品评价（公开接口，游客可看；分页加载更多）
let reviewPage = 1;            // 已加载到的页码
let reviewHasMore = false;     // 是否还有下一页
const REVIEW_SIZE = 10;        // 每页条数（与后端默认值一致）

async function loadProduct() {
    const box = document.getElementById("detail-box");
    const { status, data } = await api(`/api/products/${PRODUCT_ID}`);

    if (status === 404 || data.code === 404) {
        box.innerHTML = `
            <div class="empty-state" style="padding:60px 0">
                <div class="empty-icon">🔍</div>
                <p>商品不存在或已下架</p>
                <button class="btn-primary" onclick="location.href='/'">返回首页</button>
            </div>`;
        return;
    }
    if (data.code !== 0) {
        box.innerHTML = `<div class="empty-state" style="padding:60px 0">加载失败</div>`;
        return;
    }

    const p = data.data;
    galleryImages = p.images && p.images.length ? p.images : [p.image];
    galleryIndex = 0;
    renderDetail(p);
    startAutoPlay();

    // 商品评价区（游客可见；已下架商品只要物理记录还在，历史评价照样可查）
    reviewPage = 1;
    loadProductReviews(true);
}

// ---------- 商品评价 ----------
async function loadProductReviews(reset) {
    if (reset) reviewPage = 1;
    const section = document.getElementById("review-section");
    const { status, data } = await api(
        `/api/products/${PRODUCT_ID}/reviews?page=${reviewPage}&per_page=${REVIEW_SIZE}`
    );
    // 商品物理不存在时后端 404（详情页本身也会 404），评价区保持隐藏
    if (status === 404 || data.code === 404) return;
    section.style.display = "";
    const summaryBox = document.getElementById("review-summary");
    const listBox = document.getElementById("review-list");
    const moreBox = document.getElementById("review-more");
    if (data.code !== 0) {
        listBox.innerHTML = `<div class="empty-state">评价加载失败</div>`;
        return;
    }

    const pd = data.data;
    if (reset) {
        summaryBox.innerHTML = renderReviewSummary(pd.summary);
        listBox.innerHTML = "";
    }
    if (!pd.total) {
        listBox.innerHTML = `<div class="empty-state">
            <div class="empty-icon">💬</div><p>暂无评价</p></div>`;
        moreBox.innerHTML = "";
        reviewHasMore = false;
        return;
    }
    listBox.insertAdjacentHTML("beforeend", pd.items.map(renderReviewItem).join(""));
    reviewHasMore = reviewPage < pd.total_pages;
    moreBox.innerHTML = reviewHasMore
        ? `<button class="btn-secondary" onclick="loadMoreReviews()">加载更多评价（剩余 ${Math.max(pd.total - reviewPage * pd.per_page, 0)} 条）</button>`
        : (reset ? "" : `<span style="color:#b2bec3;font-size:13px">没有更多评价了</span>`);
}

async function loadMoreReviews() {
    reviewPage += 1;
    await loadProductReviews(false);
}

// 汇总头部：平均分数值+星星、评价数、好评率、5→1 星分布条
function renderReviewSummary(s) {
    // 无评价：后端返回结构恒定（avg null / count 0），展示占位文案而不是 0.0 分
    if (!s || !s.count) {
        return `<div style="color:#b2bec3;font-size:14px;padding:4px 0">还没有评分，快来抢沙发吧</div>`;
    }
    const avgText = Number(s.avg_rating).toFixed(1);
    const fullStars = Math.round(s.avg_rating);
    const stars = "★".repeat(fullStars) + "☆".repeat(5 - fullStars);
    const rows = [5, 4, 3, 2, 1].map(star => {
        const cnt = (s.rating_dist && s.rating_dist[star]) || 0;
        const pct = s.count ? Math.round(cnt * 100 / s.count) : 0;
        return `<div class="rs-dist-row">
                    <span class="rs-label">${star}星</span>
                    <span class="rs-bar"><i style="width:${pct}%"></i></span>
                    <span class="rs-count">${cnt}</span>
                </div>`;
    }).join("");
    return `
        <div class="review-summary">
            <div class="rs-avg">
                <div class="rs-avg-num">${avgText}</div>
                <div class="rs-avg-stars">${stars}</div>
            </div>
            <div class="rs-meta">
                共 <b>${s.count}</b> 条评价<br>
                好评率 <b style="color:#fa8c16">${s.good_rate}%</b>
            </div>
            <div class="rs-dist">${rows}</div>
        </div>`;
}

// 单条评价：星级 / 脱敏买家名 / 购买商品快照 / 内容 / 时间
function renderReviewItem(r) {
    const stars = "★".repeat(r.rating) + "☆".repeat(5 - r.rating);
    const products = (r.product_names || []).map(n => escapeHtml(n)).join("、");
    return `
        <div class="review-item">
            <div class="review-head">
                <span class="review-user">${escapeHtml(r.username || "匿名用户")}</span>
                <span class="review-stars">${stars}</span>
                <span class="review-time">${new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}</span>
            </div>
            <div class="review-products">购买商品：${products}</div>
            <div class="review-content">${escapeHtml(r.content)}</div>
        </div>`;
}

function imgSrc(name) {
    return `/static/images/${name}`;
}

// 图片加载失败兜底：先试 default.svg，再不行隐藏（与首页卡片同一套降级思路）
function imgOnError(img) {
    if (img.dataset.fb) {
        img.style.display = "none";
    } else {
        img.dataset.fb = "1";
        img.src = "/static/images/default.svg";
    }
}

function renderDetail(p) {
    const soldOut = p.stock <= 0;
    const multi = galleryImages.length > 1;

    const slides = galleryImages.map((name, i) => `
        <div class="carousel-slide">
            <img src="${imgSrc(name)}" alt="${escapeHtml(p.name)}"
                 onerror="imgOnError(this)">
        </div>`).join("");
    const arrows = multi ? `
        <button class="carousel-arrow prev" onclick="prevSlide()">‹</button>
        <button class="carousel-arrow next" onclick="nextSlide()">›</button>` : "";
    const dots = multi ? `
        <div class="carousel-dots">
            ${galleryImages.map((_, i) =>
                `<button class="carousel-dot${i === 0 ? " active" : ""}"
                         onclick="goSlide(${i})"></button>`).join("")}
        </div>` : "";

    document.getElementById("detail-box").innerHTML = `
        <div class="detail-wrap">
            <div class="gallery">
                <div class="carousel" id="carousel">
                    <div class="carousel-track" id="carousel-track">${slides}</div>
                    ${arrows}
                    ${dots}
                </div>
            </div>
            <div class="detail-info">
                <h1 class="detail-name">${escapeHtml(p.name)}</h1>
                <div class="detail-price">¥${p.price.toFixed(2)}</div>
                <div class="detail-meta">
                    库存状态：
                    <span class="stock-tag ${soldOut ? "out" : "in"}">
                        ${soldOut ? "缺货" : "有货"}
                    </span>
                    ${soldOut ? "" : `<span style="margin-left:8px">仅剩 ${p.stock} 件</span>`}
                </div>
                <div class="qty-row">
                    <label>数量</label>
                    <div class="qty-stepper">
                        <button onclick="changeQty(-1)" ${soldOut ? "disabled" : ""}>−</button>
                        <input id="buy-qty" type="number" value="1" min="1"
                               max="${p.stock}" ${soldOut ? "disabled" : ""}
                               onchange="clampQty()">
                        <button onclick="changeQty(1)" ${soldOut ? "disabled" : ""}>＋</button>
                    </div>
                </div>
                <div class="detail-actions">
                    <button class="btn-primary" id="add-cart-btn"
                            onclick="addDetailToCart()" ${soldOut ? "disabled" : ""}>
                        ${soldOut ? "已售罄" : "加入购物车"}
                    </button>
                    <button class="btn-secondary" onclick="location.href='/'">返回首页</button>
                </div>
            </div>
        </div>
        <div class="detail-desc">
            <h3>商品描述</h3>
            <div class="desc-text">${escapeHtml(p.description || "暂无描述")}</div>
        </div>
    `;
}

// ---------- 轮播控制 ----------
function updateTrack() {
    const track = document.getElementById("carousel-track");
    if (track) track.style.transform = `translateX(-${galleryIndex * 100}%)`;
    document.querySelectorAll(".carousel-dot").forEach((d, i) => {
        d.classList.toggle("active", i === galleryIndex);
    });
}

function goSlide(i) {
    if (!galleryImages.length) return;
    galleryIndex = (i + galleryImages.length) % galleryImages.length;
    updateTrack();
}

function nextSlide() { goSlide(galleryIndex + 1); }
function prevSlide() { goSlide(galleryIndex - 1); }

// 自动播放：3 秒切换、循环；只有 1 张图时不启动
function startAutoPlay() {
    stopAutoPlay();
    if (galleryImages.length > 1) {
        galleryTimer = setInterval(nextSlide, 3000);
    }
}

function stopAutoPlay() {
    if (galleryTimer) {
        clearInterval(galleryTimer);
        galleryTimer = null;
    }
}

// 鼠标悬停在轮播上时暂停自动播放，移开恢复
document.addEventListener("DOMContentLoaded", () => {
    const box = document.getElementById("detail-box");
    box.addEventListener("mouseenter", stopAutoPlay);
    box.addEventListener("mouseleave", startAutoPlay);
});

// ---------- 数量选择 ----------
function changeQty(delta) {
    const input = document.getElementById("buy-qty");
    if (!input || input.disabled) return;
    const max = parseInt(input.max, 10) || 1;
    let v = (parseInt(input.value, 10) || 1) + delta;
    if (v < 1) v = 1;
    if (v > max) v = max;
    input.value = v;
}

function clampQty() {
    const input = document.getElementById("buy-qty");
    if (!input || input.disabled) return;
    const max = parseInt(input.max, 10) || 1;
    let v = parseInt(input.value, 10);
    if (isNaN(v) || v < 1) v = 1;
    if (v > max) v = max;
    input.value = v;
}

// ---------- 加入购物车 ----------
async function addDetailToCart() {
    if (!getToken()) {
        toast("请先登录后再加入购物车", "error");
        setTimeout(() => location.href = "/login", 800);
        return;
    }
    clampQty();
    const qty = parseInt(document.getElementById("buy-qty").value, 10) || 1;
    const btn = document.getElementById("add-cart-btn");
    btn.disabled = true;
    const { status, data } = await api("/api/cart", {
        method: "POST",
        body: { product_id: PRODUCT_ID, quantity: qty }
    });
    if (status === 200 && data.code === 0) {
        toast(`已加入购物车 × ${qty}`, "success");
        updateCartBadge();
    } else {
        toast(data.msg || "添加失败", "error");
    }
    btn.disabled = false;
}

loadProduct();
