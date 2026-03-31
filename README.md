[中文](README.md) | [English](README.en.md)

# ISBN 规范化器

为萌娘百科 wikitext中的 `{{ISBN|...}}` 模板提供独立的规范化工具和 MediaWiki 机器人。

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

## 工作方式

### 命令行工具

**单文件格式化：**
```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format \
  --in-place
```

**转换 + 转换为 ISBN-13：**
```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format -to13 \
  --in-place
```

**转换 + 删除相同的标签：**
```bash
python isbn_normalise.py \
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

在 `.github/workflows/isbn-normaliser-bot.yml` 中配置：

1. **手动触发（workflow_dispatch）**
   - 可选输入 `max_edits` 来限制本次编辑数量
   - 访问 Actions 标签页点击"运行工作流"

2. **定时执行（可选）**
   - 取消注释 `schedule` 部分启用每日 UTC 03:30 执行

## 环境变量配置

在 `.env` 或 GitHub Secrets 中设置：

```
BOT_USERNAME=YourBotName
BOT_PASSWORD=YourBotPassword
```

默认值：
- `WIKI_API` — 默认为 `https://mzh.moegirl.org.cn/api.php`
- `USER_AGENT` — 默认为 `ISBNNormaliserBot/1.0 (...)`

## 规范化规则

- 始终规范化模板第 1 参数（当有效时）
- 默认保持第 2 参数不变
- 仅在显式启用且语义相同时删除第 2 参数
- 编辑摘要：`根据 ISO 2108:2017（https://www.iso.org/standard/65483.html ）调整ISBN`

## 参考资料

- [ISO 2108:2017](https://www.iso.org/standard/65483.html)
- [国际 ISBN 中心](https://www.isbn-international.org/range_file_generation)
