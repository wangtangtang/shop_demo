---
AIGC:
    Label: "1"
    ContentProducer: 001191110102MACQD9K64018705
    ProduceID: 2398458658700201_0-data_volume/7650143508224885028-files/所有对话/主对话/README_shop_demo.md
    ReservedCode1: ""
    ContentPropagator: 001191110102MACQD9K64028705
    PropagateID: 2398458658700201#1791338557830
    ReservedCode2: ""
---
# 极客商城 shop_demo

基于 **Flask** 的电商系统，实现了从商品浏览、购物车、下单支付、物流、评价到售后的完整交易闭环，并内置智能客服、售后智能初审、评价标签分析、经营数据看板、RFM 用户分层等 AI / 规则能力。项目同时配套了一套 `pytest + requests` 接口自动化测试。

> 毕业设计 / 接口自动化测试练习项目。默认使用本地 SQLite，无需安装数据库即可运行；也支持切换到 MySQL 8。

---

## 一、技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | Flask 3、Flask-SQLAlchemy（ORM）、蓝图（Blueprint） |
| 数据库 | SQLite（默认，零配置）/ MySQL 8（pymysql 驱动，双兼容） |
| 认证 | PBKDF2-SHA256 加盐哈希密码 + HMAC-SHA256 签名限时 Token（纯标准库实现） |
| 前端 | HTML5 / CSS3 / 原生 JavaScript（服务端渲染页面 + fetch 调接口） |
| 自动化测试 | pytest、requests、Flask 测试客户端 |
| 持续集成 | GitHub Actions（每次推送自动跑冒烟测试） |

---

## 二、功能一览

**顾客端**
- 注册 / 登录 / 退出（密码加盐哈希存储，登录返回签名 Token）
- 商品列表（分页、关键字搜索）、商品详情
- 购物车：加购、修改数量、删除、勾选部分商品结算
- 订单：提交订单、订单状态流转、订单列表 / 详情、地址快照
- 支付：统一下单（微信 / 支付宝）、模拟支付网关异步回调、支付单关闭与重开
- 物流：管理员发货、四段式物流轨迹查询
- 评价：订单完成后评价（1-5 星 + 文字）、商品评价列表
- 售后：退货退款 / 换货 / 价保三种申请，查看审核进度
- 收货地址簿：增删改查、默认地址、数据隔离

**管理后台**
- 订单管理：订单列表、状态筛选、订单发货
- 商品管理：新增 / 修改 / 下架（软删除，可恢复）、多图
- 售后审核：查看 AI 初审建议，人工 approve / reject（**人有最终裁定权**）
- 评价管理：查看、软删除评价
- 数据看板：
  - 一期：核心指标概览、销售趋势、热销商品、转化漏斗、AI 售后效果面板、AI 经营简报
  - 二期：评价标签洞察、库存预警、RFM 用户分层

**AI / 规则能力（均为离线纯函数模块，可独立替换为大模型）**
- 智能客服：意图识别、查订单、商品咨询、推荐、退货引导；含提示词注入拦截
- 售后智能初审：根据申请文本给出 approve / reject / manual 建议及可解释理由
- 评价标签与情感分析：自动抽取正 / 负向标签、判断情感倾向
- AI 经营简报：模板式自然语言生成（NLG）
- RFM 用户分层：按最近消费时间、频次、金额做客户分层

---

## 三、目录结构

```
shop_demo/
├── run.py                     # 启动入口
├── config.py                  # 真实配置（不提交，见 .gitignore）
├── config.example.py          # 配置模板（提交到仓库）
├── conftest.py                # pytest 公共 fixture / 工具函数
├── reset_db.py                # 重置数据库
├── migrate_*.py               # 历次功能的数据库升级脚本（老库平滑升级）
├── pytest.ini                 # pytest 配置
├── requirements.txt           # 依赖清单
├── README.md
├── .github/workflows/ci.yml   # GitHub Actions 持续集成
├── app/
│   ├── __init__.py            # create_app 应用工厂、建表、老库补列
│   ├── routes.py              # 全部 API 接口（api 蓝图，前缀 /api）
│   ├── pages.py               # 前端页面路由（含 /admin 服务端鉴权）
│   ├── models.py              # 数据模型（User/Product/Order/...）
│   ├── security.py            # 密码哈希 + Token 签名
│   ├── seed.py                # 初始化示例商品与管理员
│   ├── payment.py             # 支付网关（下单 / 支付成功处理）
│   ├── ai_service.py          # 智能客服 + 推荐
│   ├── aftersale_ai.py        # 售后智能初审规则
│   ├── review_ai.py           # 评价标签 + 情感分析
│   ├── dashboard_brief.py     # AI 经营简报（NLG）
│   ├── rfm_analysis.py        # RFM 用户分层
│   ├── templates/             # HTML 页面
│   └── static/                # CSS / JS / 图片
├── scripts/
│   └── cleanup_top_history.py # 清理热销榜历史数据
└── test_api/                  # 接口自动化测试（pytest）
    ├── test_smoke_ci.py       # CI 冒烟测试（临时 SQLite，不依赖外部服务）
    ├── test_user.py
    ├── test_addresses.py
    ├── test_admin.py / test_admin_products.py
    ├── test_products.py / test_cart.py
    ├── test_payment.py / test_logistics.py
    ├── test_reviews.py / test_search.py
    ├── test_security.py / test_soft_delete.py
    ├── test_pagination.py
    ├── test_aftersale.py / test_aftersale_ai.py
    ├── test_ai.py
    ├── test_fixes_1003.py
    ├── test_admin_stats.py / test_dashboard_phase2.py
```

---

## 四、快速开始

### 1. 环境准备
- Python 3.10+
- （可选）MySQL 8，不装也能用 SQLite 直接跑

### 2. 克隆并安装依赖
```bash
git clone <仓库地址>
cd shop_demo
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. 准备配置
```bash
# Windows
copy config.example.py config.py
# macOS / Linux
cp config.example.py config.py
```
默认配置即可用 SQLite 直接运行，无需改动。

### 4. 启动服务
```bash
python run.py
```
浏览器访问：http://127.0.0.1:5000

**默认账号**
- 管理员：`admin` / `admin123`（后台入口 http://127.0.0.1:5000/admin）
- 顾客：可在注册页自行注册

### 5.（可选）切换到 MySQL
不修改代码，通过环境变量指定连接串：
```bash
# Windows CMD
set DATABASE_URL=mysql+pymysql://root:你的密码@localhost:3306/shop_demo?charset=utf8mb4
# Windows PowerShell
$env:DATABASE_URL="mysql+pymysql://root:你的密码@localhost:3306/shop_demo?charset=utf8mb4"
# macOS / Linux
export DATABASE_URL="mysql+pymysql://root:你的密码@localhost:3306/shop_demo?charset=utf8mb4"
```
然后正常 `python run.py`，首次启动会自动建表并写入示例数据。

---

## 五、接口设计约定

- API 统一前缀 `/api`，页面路由无前缀。
- 统一响应格式：
  ```json
  { "code": 0, "msg": "success", "data": {} }
  ```
  `code = 0` 表示业务成功，非 0 为业务错误码。
- 分页接口统一返回：
  ```json
  { "items": [], "total": 0, "page": 1, "per_page": 10, "total_pages": 1 }
  ```
- 认证方式：请求头携带 `Authorization: Bearer <token>`。

---

## 六、运行测试

测试分为两类：

**1）CI 冒烟测试（推荐先跑，零外部依赖）**
使用 Flask 测试客户端 + 临时 SQLite，不连真实数据库、不起服务：
```bash
pytest test_api/test_smoke_ci.py -v
```

**2）完整接口测试**
需要先启动服务（`python run.py`），再通过 requests 对真实接口发请求：
```bash
pytest test_api -v
```

---

## 七、持续集成（CI）

仓库配置了 GitHub Actions（`.github/workflows/ci.yml`）：每次向 `main` / `master` 推送或提交 Pull Request 时，云端会在干净的 Ubuntu 环境中自动安装依赖并运行 `test_smoke_ci.py`，通过或失败以红 / 绿灯展示。云端环境没有 MySQL、也不启动服务，因此冒烟测试专门使用临时 SQLite。

---

## 八、设计亮点

1. **人在回路（Human-in-the-loop）**：售后 AI 只产出建议与理由，绝不自动改状态；状态变更的唯一入口是管理员人工审核，人工可以双向推翻 AI 建议。
2. **AI 可测性**：所有 AI 能力都是离线纯函数 / 规则模块，输入确定则输出确定，测试稳定可复现，也可整体替换为大模型并保留降级方案。
3. **安全设计**：密码加盐哈希、Token 签名防伪造、水平 / 垂直越权防护、提示词注入拦截、敏感字段不出现在任何响应中。
4. **数据一致性**：取消订单回补库存、支付回调幂等、地址 / 商品名下单时快照、超时未支付订单惰性关单。
5. **老库平滑升级**：通过 `migrate_*.py` 与启动时自动补列，无需删库即可演进。

---

## 九、已知局限（持续学习中）

- 超时订单采用「访问时惰性关单」，没有后台定时任务批量处理，长时间无人访问的订单不会被立即关闭。
- 部分列表接口存在 N+1 查询，数据量大时可进一步优化。

---

> 本内容由 Coze AI 生成，请遵循相关法律法规及《人工智能生成合成内容标识办法》使用与传播。
