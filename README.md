[中文](README.md) | [English](README.en.md)

# ISBN 规范化器

为萌娘百科 wikitext中的{{[ISBN](https://mzh.moegirl.org.cn/Template:ISBN)}}模板提供独立的规范化工具和 MediaWiki 机器人，规范化脚本可以单独运行。

## 核心功能

- **isbn_normalise.py** — 纯 ISBN 规范化库
  - 按国际标准书号规则对 ISBN-10/13 进行连字符规范化
  - 可选：将 ISBN-10 转换为 ISBN-13
  - 可选：删除语义相同的第二参数

- **mw_isbn_bot.py** — MediaWiki 机器人运行时
  - 自动获取嵌入 Template:ISBN 和其重定向的页面
  - 支持分页查询（自动处理 continue）
  - 检查 Allowbots 规则后再编辑
  - 支持编辑数量上限控制

## 依赖资源

- `RangeMessage.xml` — 国际 ISBN 中心提供的范围消息文件

## 环境与依赖

- Python 3.10+（推荐 3.11，与 GitHub Actions 一致）
- 第三方依赖：
  - `requests`
  - `brotli`
  - `mwparserfromhell`

快速安装：

```bash
python -m pip install --upgrade pip
pip install requests brotli mwparserfromhell
```

## 快速开始

```bash
git clone https://github.com/kelly/ISBN-normaliser.git
cd ISBN-normaliser
python -m pip install --upgrade pip
pip install requests brotli mwparserfromhell
```

然后准备 `RangeMessage.xml`（仓库已包含），即可按下文命令运行。

## 工作方式

### 命令行工具

**单文件格式化：**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  --text-file your_wikitext.txt \
  -format \
  --in-place
```

**单个 ISBN 规范化：**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  9787302511625
```

**转换 + 转换为 ISBN-13：**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  --text-file your_wikitext.txt \
  -format -to13 \
  --in-place
```

**转换 + 删除相同的标签：**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  --text-file your_wikitext.txt \
  -format \
  --drop-equal-label \
  --in-place
```

### MediaWiki 机器人

**本地运行（需要 .env 文件或命令行参数）：**
```bash
python mw_isbn_bot.py \
  --wiki-api https://example.org/api.php \
  --bot-username MyBot \
  --bot-password MyBotPassword \
  --max-edits 10
```

**干运行（测试不保存）：**
```bash
python mw_isbn_bot.py \
  --wiki-api https://example.org/api.php \
  --bot-username MyBot \
  --bot-password MyBotPassword \
  --dry-run
```

## GitHub Actions 自动化

### 1) ISBN 机器人工作流

文件：`.github/workflows/isbn-normaliser-bot.yml`

1. **手动触发（workflow_dispatch）**
  - 可选输入 `max_edits` 来限制本次编辑数量
  - 访问 Actions 标签页点击"运行工作流"

2. **定时执行（可选）**
  - 当前 `schedule` 已注释，可按需取消注释启用
  - 计划时间为 UTC `20:15`（cron: `15 20 * * *`）

### 2) RangeMessage 自动更新工作流

文件：`.github/workflows/update-rangemessage.yml`

1. **触发方式**
  - 支持手动触发（`workflow_dispatch`）
  - 每周三 UTC `03:05` 自动执行（cron: `05 3 * * 3`）

2. **行为说明**
  - 下载最新 `RangeMessage.xml`
  - 若文件有变化，自动提交并推送到当前分支

3. **权限要求**
  - 工作流已声明 `contents: write`，用于提交更新

## 文件说明

- `isbn_normalise.py`：
  - ISBN 规范化核心逻辑
  - 支持单个 ISBN 输出与 wikitext 模板批量处理

- `mw_isbn_bot.py`：
  - MediaWiki 机器人入口
  - 负责登录、分页拉取页面、检查 Allowbots、提交编辑

- `RangeMessage.xml`：
  - ISBN 号段规则来源文件
  - 由国际 ISBN 中心发布，供规范化算法使用

- `.github/workflows/isbn-normaliser-bot.yml`：
  - 机器人执行工作流（手动触发，可选定时）

- `.github/workflows/update-rangemessage.yml`：
  - 自动更新 `RangeMessage.xml` 的工作流

## 环境变量配置

在 `.env` 或 GitHub Secrets 中设置：

```
BOT_USERNAME=YourBotName
BOT_PASSWORD=YourBotPassword
```

默认值：
- `WIKI_API` — 脚本内置默认值为 `https://mzh.moegirl.org.cn/api.php`
- `USER_AGENT` — 脚本内置默认值为 `ISBNNormaliserBot/1.0 (...)`

说明：
- 当前版本仅从 `.env` 读取 `BOT_USERNAME` 与 `BOT_PASSWORD`
- 其余参数（如 `--max-edits`、`--dry-run`、`--to13` 等）请通过命令行传入

## 规范化规则

- 始终规范化模板第 1 参数（当有效时）
- 默认保持第 2 参数不变
- 仅在显式启用且语义相同时删除第 2 参数
- 编辑摘要：`根据 ISO 2108:2017（https://www.iso.org/standard/65483.html ）自动调整ISBN（若阁下对此次修改感到疑惑，可以前往 https://grp.isbn-international.org/ 查找出版社前缀信息）`

## 故障排查

- 报错 `ModuleNotFoundError`：
  - 先执行 `pip install requests brotli mwparserfromhell`

- 报错找不到 `RangeMessage.xml`：
  - 确认当前目录存在该文件，或通过 `--xml` 指定正确路径

- 机器人未执行编辑：
  - 先用 `--dry-run` 查看是否检测到可修改页面
  - 检查 `--max-edits` 是否设置为 0
  - 检查机器人账号权限与站点的 Allowbots/编辑限制策略

- GitHub Actions 未产生提交：
  - `update-rangemessage` 在文件无变化时会显示 “No changes to commit”，这是正常行为

## 参考资料

- [ISO 2108:2017](https://www.iso.org/standard/65483.html)
- [国际 ISBN 中心](https://www.isbn-international.org/range_file_generation)
