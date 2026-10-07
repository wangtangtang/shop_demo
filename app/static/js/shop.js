// ========== 首页商品列表（分页 + 搜索） ==========
const PRODUCT_ICONS = ["⌨️", "🖱️", "🖥️", "🔌", "🎧"];
let productPage = 1;    // 当前页
let productSize = 12;   // 每页条数（与后端默认 per_page=12 一致）
let productKeyword = ""; // 当前搜索关键字（翻页时带上）

// 商品卡片 HTML（首页列表 / 猜你喜欢共用）。
// 卡片整体可点击跳详情页；卡片上的「+加购」按钮 stopPropagation，
// 点按钮加购、点卡片其他区域跳详情，两个行为不冲突。
function productCardHtml(p) {
    const icon = PRODUCT_ICONS[(p.id - 1) % PRODUCT_ICONS.length];
    const soldOut = p.stock <= 0;
    // 列表卡片只显示第一张图（多图在详情页轮播展示）
    const firstImg = (p.images && p.images.length) ? p.images[0] : p.image;
    const imgSrc = `/static/images/${firstImg}`;
    return `
        <div class="product-card" onclick="goProductDetail(${p.id})"
             style="cursor:pointer">
            <div class="product-img">
                <img src="${imgSrc}"
                     alt="${p.name}"
                     onerror="if(this.dataset.fb){this.parentElement.innerHTML='${icon}'}else{this.dataset.fb=1;this.src='/static/images/default.svg'}">
            </div>
            <div class="product-info">
                <div class="product-name" title="${p.name}">${p.name}</div>
                <div class="product-desc">${p.description || ""}</div>
                <div class="product-footer">
                    <div>
                        <div class="product-price">${p.price.toFixed(2)}</div>
                        <div class="product-stock ${soldOut ? 'out' : ''}">
                            ${soldOut ? '已售罄' : `库存 ${p.stock}`}
                        </div>
                    </div>
                    <button class="btn-cart" onclick="event.stopPropagation();addToCart(${p.id})" ${soldOut ? 'disabled' : ''}>+</button>
                </div>
            </div>
        </div>
    `;
}

async function goProduct(page, size) {
    productPage = page;
    if (size) productSize = size;
    await loadProducts(productKeyword);
}

// 跳商品详情页（轮播图 + 描述）
function goProductDetail(pid) {
    location.href = `/product/${pid}`;
}

async function loadProducts(keyword = "") {
    productKeyword = keyword;
    const grid = document.getElementById("product-grid");
    const pagerBox = document.getElementById("product-pager");
    const qs = new URLSearchParams({ page: productPage, per_page: productSize });
    if (keyword) qs.set("keyword", keyword);
    const { data } = await api(`/api/products?${qs.toString()}`);

    if (data.code !== 0) {
        grid.innerHTML = `<div class="empty-state">加载失败</div>`;
        pagerBox.style.display = "none";
        return;
    }

    const pd = data.data;  // 分页结构：{items, total, page, per_page, total_pages}
    if (!pd.total) {
        grid.innerHTML = `<div class="empty-state">没有找到相关商品</div>`;
        pagerBox.style.display = "none";
        return;
    }
    if (!pd.items.length) {
        grid.innerHTML = `<div class="empty-state">本页没有商品，请点下方分页栏翻页</div>`;
        renderPager(pagerBox, pd, goProduct, [12, 24, 50]);
        return;
    }

    grid.innerHTML = pd.items.map(productCardHtml).join("");
    renderPager(pagerBox, pd, goProduct, [12, 24, 50]);
}

async function addToCart(pid) {
    if (!getToken()) {
        toast("请先登录", "error");
        setTimeout(() => location.href = "/login", 800);
        return;
    }
    const { status, data } = await api("/api/cart", {
        method: "POST",
        body: { product_id: pid, quantity: 1 }
    });
    if (status === 200 && data.code === 0) {
        toast("已加入购物车", "success");
        updateCartBadge();
    } else {
        toast(data.msg || "添加失败", "error");
    }
}

function searchProducts() {
    productPage = 1;  // 重新搜索回到第 1 页
    const kw = document.getElementById("search-input").value.trim();
    loadProducts(kw);
}

document.getElementById("search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchProducts();
});

loadProducts();

// ========== AI 猜你喜欢（首页推荐区，登录后个性化） ==========
async function loadRecommend() {
    const box = document.getElementById("recommend-grid");
    if (!box) return;  // 非首页没有这个区块
    const { data } = await api("/api/ai/recommend");
    if (data.code !== 0 || !data.data || !data.data.length) {
        document.getElementById("recommend-section").style.display = "none";
        return;
    }
    document.getElementById("recommend-title").textContent =
        getToken() ? "✨ 猜你喜欢（根据你的购物偏好）" : "🔥 热销推荐（登录后个性化推荐）";

    // 推荐接口仍是数组返回（不走 /products 分页），卡片复用同一套渲染
    box.innerHTML = data.data.map(productCardHtml).join("");
}

loadRecommend();
