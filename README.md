# News Skill Agent

一个可直接运行的 Python 自动化项目，用于每天抓取多个资讯站点的昨天和今天新闻，提取标题、摘要、发布时间、链接、来源，自动打标签，生成 Excel，并推送摘要到飞书。

## 项目简介

这个项目面向“稳定、可运行、便于扩展”的资讯自动化闭环，当前优先完成：

- 多站点配置化管理
- 通用新闻列表页抓取
- 昨天和今天的资讯过滤
- 发布时间无法解析时保留文章
- 自动标签分类
- Excel 导出
- 同时导出全量资讯表和大模型筛选后的资讯表
- 飞书文本通知
- APScheduler 每天 9 点调度

当前不包含数据库、分布式组件、前端页面和容器化部署。

## 功能说明

- 从 `config/sources.yaml` 读取多个资讯源
- 每个资讯源支持多个列表页
- 每个资讯源支持 `groups` 分组，可按分类拆成多个自动化分别运行
- 按来源站点时区计算“昨天和今天”的自然日窗口
- 抓取列表页，必要时进入详情页补摘要和发布时间
- 对部分来源，摘要会直接根据详情页正文生成，并显式标记为程序生成摘要
- 对资讯打上以下标签：
  - 新品上市
  - 企业融投资
  - 企业上市
  - 技术创新
  - 政策法规
  - 未分类
- 去重后导出到 `.xlsx`
- 推送飞书文本摘要通知
- 提供 `--run-once` 调试入口和常驻调度入口
- 支持 `--group` 按分组运行，便于拆分“新媒体”“巨头官网”“国家政府”等任务

## 项目结构说明

```text
news_skill_agent/
├── app/
│   ├── crawlers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── custom_parsers.py
│   │   ├── factory.py
│   │   └── generic_news_parser.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── dates.py
│   │   ├── dedupe.py
│   │   └── logging_utils.py
│   ├── __init__.py
│   ├── config.py
│   ├── excel_writer.py
│   ├── feishu.py
│   ├── main.py
│   ├── models.py
│   ├── pipeline.py
│   ├── scheduler.py
│   ├── sources_loader.py
│   └── tagging.py
├── config/
│   └── sources.yaml
├── output/
├── tests/
│   ├── fixtures/
│   │   └── generic_list.html
│   ├── test_dates.py
│   ├── test_dedupe.py
│   ├── test_excel_writer.py
│   ├── test_feishu_payload.py
│   └── test_tagging.py
├── .env.example
├── README.md
└── requirements.txt
```

## 安装步骤

1. 进入目录：

```bash
cd /Users/ruiyi/Documents/Playground/news_skill_agent
```

2. 创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 复制环境变量文件：

```bash
cp .env.example .env
```

## `.env` 配置方法

示例：

```env
APP_ENV=development
LOG_LEVEL=INFO
APP_TIMEZONE=Asia/Shanghai
OUTPUT_DIR=/Users/ruiyi/Documents/Playground/news_skill_agent/output
SOURCES_FILE=/Users/ruiyi/Documents/Playground/news_skill_agent/config/sources.yaml
REQUEST_TIMEOUT_SECONDS=15
MAX_DETAIL_FETCH_PER_SOURCE=10
MAX_ITEMS_PER_SOURCE=30
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token
FEISHU_SECRET=
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_RECEIVE_ID_TYPE=
FEISHU_RECEIVE_ID=
ENABLE_FEISHU=true
INCLUDE_ITEMS_WITHOUT_PARSED_DATE=true
SCHEDULE_HOUR=9
SCHEDULE_MINUTE=0
```

字段说明：

- `APP_TIMEZONE`：调度器默认时区
- `OUTPUT_DIR`：Excel 输出目录
- `SOURCES_FILE`：资讯源配置文件
- `REQUEST_TIMEOUT_SECONDS`：HTTP 请求超时
- `MAX_DETAIL_FETCH_PER_SOURCE`：每站最多进入多少个详情页补抓
- `MAX_ITEMS_PER_SOURCE`：每站最多处理多少条候选新闻
- `ENABLE_FEISHU`：是否启用飞书推送
- `FEISHU_WEBHOOK_URL`：飞书机器人 webhook 地址
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：如需把 Excel 作为飞书文件消息发送，配置飞书应用凭证
- `FEISHU_RECEIVE_ID_TYPE` / `FEISHU_RECEIVE_ID`：文件消息投递目标，例如 `chat_id`

## `sources.yaml` 配置示例

```yaml
- name: Example Tech News
  base_url: https://example.com
  list_urls:
    - https://example.com/news
    - https://example.com/company-news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  date_format_hint:

- name: Example Global Industry News
  base_url: https://news.example.org
  list_urls:
    - https://news.example.org/latest
  enabled: true
  parser_type: generic
  timezone: UTC
  date_format_hint: "%Y-%m-%d %H:%M:%S"
```

字段说明：

- `name`：来源名称
- `base_url`：站点主域名或栏目根路径
- `list_urls`：可抓取的新闻列表页
- `enabled`：是否启用
- `parser_type`：`generic` 或自定义 `custom_xxx`
- `timezone`：该资讯源发布时间的时区
- `date_format_hint`：已知时间格式时可显式提示
- `groups`：来源所属分组，可写多个，例如 `新媒体`、`巨头官网`、`国家政府`
- `query_params`：供 custom parser 使用的额外接口参数，例如巨潮资讯公告接口的 `column`、`searchkey`、`pageSize`

示例：

```yaml
- name: Example Startup Media
  groups:
    - 新媒体
    - 国内媒体
  base_url: https://example.com
  list_urls:
    - https://example.com/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  date_format_hint:
```

## 如何立即运行一次

```bash
python -m app.main --run-once
```

按分组运行：

```bash
python -m app.main --run-once --group 新媒体
python -m app.main --run-once --group 巨头官网
python -m app.main --run-once --group 国家政府
```

查看当前可用分组：

```bash
python -m app.main --list-groups
```

## 如何启动每天 9 点自动执行

```bash
python -m app.main
```

项目会进入调度状态，并按 `.env` 中配置的 `SCHEDULE_HOUR` 与 `SCHEDULE_MINUTE` 执行。

如果你想把单个大任务拆成多个自动化，推荐做法是：

- 一个自动化只跑一个 `--group`
- 同一个 Skill 复用同一个项目，不复制多份配置
- 让不同自动化分别负责 `新媒体`、`巨头官网`、`国家政府`、`行业媒体` 等分组
- 这样某一组来源失败时，不会拖垮其他组的抓取和推送

## Excel 输出说明

- 文件名格式：
  - 全量资讯：`news_report_all_YYYY-MM-DD_HHMMSS.xlsx`
  - 筛选后资讯：`news_report_filtered_YYYY-MM-DD_HHMMSS.xlsx`
- 默认输出到 `output/`
- 工作表名：`news`
- 列固定为：
  - `Source`
  - `Title`
  - `Summary`
  - `Published At`
  - `URL`
  - `标签`
- 如果发布时间无法解析，`Published At` 为空单元格
- 如果没有抓到数据，也会生成只含表头的空表

## 摘要字段说明

- `Summary` 字段现在分为两种来源：
  - 站点列表页或接口原始附带的摘要
  - 程序根据详情页正文生成的摘要
- 如果某条摘要是程序根据详情页正文生成的，会统一带前缀：
  - `【程序生成摘要】`
- 这样可以直接区分：
  - 哪些摘要来自站点原文
  - 哪些摘要来自程序二次提取
- 当前已经启用“程序生成摘要”的来源包括：
  - `新京报`
  - `FoodBev`
  - `百事公司`
  - `百事公司大中华区`
  - `36氪`
  - `FoodTalks /flash/`
  - `澎湃快讯`
- 这些来源通常会优先从详情页正文提取前 1 到 2 段，做适度截断后写入 `Summary`
- `新京报` 当前已接入详情页提取时间和程序摘要，但其分页地址目前会返回 `405`，因此程序会稳定抓取第一页，并在翻页被拒绝时自动停止，不会让整条任务失败
- `澎湃快讯` 当前通过 Next.js 数据入口抓取，属于稳定首屏版：可以稳定提取首屏快讯的标题、时间、正文摘要和详情链接，但还未扩展到滚动分页版
- `巨潮资讯公告` 现已支持通过 `POST /new/hisAnnouncement/query` 接口抓取，不走前端壳页面；适合用 `query_params` 限定市场、关键词或页数
- `FoodTalks /news` 现已支持通过 `api-we.foodtalks.cn/news/news/page` 接口抓取；直接抓页面 HTML 只会拿到前端壳，当前 parser 会先预热页面再带 cookie 请求接口

## 飞书推送说明

当前实现：

- 支持飞书机器人 webhook 文本通知
- 如同时配置飞书应用凭证和接收目标，可额外上传并发送两份 Excel 文件消息
- 消息包含执行时间、站点统计、全量条数、筛选后条数、未解析发布时间条数，以及两份 Excel 的文件名和路径

## 大模型筛选说明

- 可选对 `界面新闻`、`36氪` 等高噪音来源启用大模型行业相关性筛选
- 当前筛选口径会优先保留食品饮料、餐饮、咖啡茶饮、乳品、零食、酒饮、保健营养、食品供应链、冷链、原料、包装、设备、政策监管相关资讯
- 输出会同时保留：
  - 全量资讯表：便于回看模型筛掉了什么
  - 筛选后资讯表：便于直接用于简报和飞书
- 当前实际生效规则如下：
  - 只对 `LLM_FILTER_SOURCES` 中的来源做大模型判断，默认是 `界面新闻`、`36氪`
  - `北京商报` 这类未列入 `LLM_FILTER_SOURCES` 的来源不会经过大模型，会直接进入结果
  - 每个来源最多送 `LLM_FILTER_MAX_ITEMS` 条给模型判断，当前是“每个来源最多 120 条”，不是所有来源共用 120 条
  - 模型输入字段包括：来源、标题、摘要、链接
  - 模型输出固定为 3 个字段：`is_food_related`、`reason`、`topic`
  - 当前 prompt 偏向“宁可多收，不要漏掉”，只要新闻主体和食品饮料、餐饮、咖啡茶饮、乳品、保健营养、食品零售、食品供应链等方向有实质关联，就倾向保留
  - 只有当新闻主体明显属于汽车、半导体、通用软件、地产、无关金融等行业时，才倾向判为不相关
  - 如果模型调用失败，程序不会中断，也不会直接丢弃新闻，而是保留原新闻继续后续流程
  - `36氪` 额外有一层更严格的兜底规则：即使模型判为相关，也必须在标题、摘要、模型给出的 `topic` 或 `reason` 中命中食品行业显式信号词，才会真正进入筛选结果
  - `36氪` 当前显式信号词包括：`食品`、`饮料`、`餐饮`、`咖啡`、`茶饮`、`乳品`、`火锅`、`零食`、`保健`、`营养`、`益生菌`、`商超`、`超市`、`生鲜`、`预制菜`、`食材`、`供应链`，以及 `海底捞`、`胖东来`、`赵一鸣`、`伊利`、`蒙牛`、`优思益`、`皮爷咖啡` 等品牌词

当前限制：

- 自定义机器人 webhook 仍只负责文本摘要
- 如果要把本地 Excel 直接发到飞书，需要额外配置飞书应用凭证和接收目标

## 常见错误排查

### 1. 运行后没有抓到新闻

- 检查 `sources.yaml` 中的列表页 URL 是否能直接打开
- 检查目标站点是否依赖 JavaScript 动态渲染
- 打开 `LOG_LEVEL=DEBUG` 观察日志

### 2. 发布时间经常为空

- 说明站点列表页和详情页都没有标准时间字段，或格式不在当前解析范围内
- 可以为该站点补 `date_format_hint`
- 或为该站点编写 `custom parser`

### 3. 飞书没有收到消息

- 检查 `ENABLE_FEISHU=true`
- 检查 `FEISHU_WEBHOOK_URL` 是否有效
- 检查网络是否允许访问飞书 webhook

### 4. Excel 能生成但内容很少

- 通用 parser 是保守策略，不会做全站扫描
- 如果目标站点结构特殊，建议编写自定义 parser

## 如何新增一个资讯网站

### 通用站点

如果站点是普通静态新闻列表页：

1. 在 `config/sources.yaml` 新增一个 source
2. `parser_type` 填 `generic`
3. 填写正确的 `timezone`
4. 运行 `python -m app.main --run-once` 验证结果

### 需要自定义 parser 的站点

以下情况通常需要自定义解析器：

- 页面依赖 JavaScript 渲染
- 新闻卡片结构很特殊
- 发布时间埋在特殊 JSON 或脚本里
- 需要特殊鉴权或请求头

做法：

1. 在 `app/crawlers/custom_parsers.py` 增加新 parser
2. 在 `app/crawlers/factory.py` 注册 `custom_xxx`
3. 在 `sources.yaml` 将 `parser_type` 改为对应值

## 通用 parser 适用范围

适合：

- 静态 HTML 新闻列表页
- 常见 `article` / `li` / `div` 卡片结构
- 发布时间能从 `time`、meta、JSON-LD 或正文中识别

不适合：

- 强依赖浏览器执行脚本后才出现内容的页面
- 需要登录、验证码、复杂反爬策略的页面
- 完全非标准、且发布时间隐藏很深的页面

## 当前限制与后续扩展方向

当前限制：

- 规则打标依赖关键词，不具备语义理解
- 通用 parser 不是万能解析器
- 真实联网抓取测试未内置在单元测试里

后续可扩展方向：

- 为重点站点补 custom parser
- 增加代理、重试、限速和更细的请求配置
- 接入飞书文件上传
- 用模型或更复杂规则提升标签准确率与食品行业相关性筛选
- 增加 HTML fixture 解析测试和 mock 网络测试

## 可选：大模型食品行业筛选

项目支持可选的大模型筛选开关，用于判断新闻是否和食品饮料/餐饮行业相关。

配置项：

```env
ENABLE_LLM_FOOD_FILTER=true
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.2
LLM_FILTER_SOURCES=界面新闻,36氪
LLM_FILTER_MAX_ITEMS=120
```

行为说明：

- 默认关闭
- 开启后，仅对 `LLM_FILTER_SOURCES` 中的网站做相关性判断
- 当前默认只对 `界面新闻` 和 `36氪` 启用
- `LLM_FILTER_MAX_ITEMS` 按“单个来源”生效，不是全局共享上限
- `36氪` 会额外应用一层显式食品信号词兜底，减少泛商业内容误入
- 如果模型调用失败，程序会保留原新闻，不会中断主流程
