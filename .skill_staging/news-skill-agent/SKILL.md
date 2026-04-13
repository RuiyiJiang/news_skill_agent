---
name: news-skill-agent
description: Use this skill when working with the local news crawling automation project at /Users/ruiyi/Documents/Playground/news_skill_agent, especially to configure sources, run the crawler once, inspect Excel output, troubleshoot Feishu webhook delivery, extend parsers, or maintain the scheduled news collection workflow.
metadata:
  short-description: Operate the local news crawler skill
---

# News Skill Agent

This skill is for operating and extending the local project at `/Users/ruiyi/Documents/Playground/news_skill_agent`.

中文说明：
这个 Skill 用来维护和运行本地的资讯抓取项目。它可以读取配置中的资讯网站，抓取昨天和今天的新闻，生成 Excel，并把结果推送到飞书。

Use it when the task is to:
- inspect or modify `sources.yaml` and `.env`
- run the crawler once and inspect output files
- debug parsing, tagging, deduplication, Excel generation, or Feishu payloads
- add a new generic source or implement a custom parser
- verify tests before shipping changes

## Project Location

- Project root: `/Users/ruiyi/Documents/Playground/news_skill_agent`
- Main entrypoint: `/Users/ruiyi/Documents/Playground/news_skill_agent/app/main.py`
- Source config: `/Users/ruiyi/Documents/Playground/news_skill_agent/config/sources.yaml`
- Environment template: `/Users/ruiyi/Documents/Playground/news_skill_agent/.env.example`

中文提示：
- `sources.yaml` 用来配置网站来源
- `.env` 用来配置飞书 webhook、输出目录和调度参数
- `output/` 目录里会生成 Excel 结果

## Fast Path

1. Read `README.md` if the task is broad or configuration-related.
2. Check `config/sources.yaml` and `.env` before running anything.
3. Use the local Python 3.11 virtualenv in the project root when available.
4. For a single execution, run:

```bash
.venv/bin/python -m app.main --run-once
```

5. For tests, run:

```bash
.venv/bin/python -m pytest
```

中文快速使用：
1. 想立即抓一次，就运行 `.venv/bin/python -m app.main --run-once`
2. 想修改资讯来源，就编辑 `config/sources.yaml`
3. 想检查结果，就查看 `output/` 下最新的 Excel 文件
4. 想确认代码是否正常，就运行 `.venv/bin/python -m pytest`

## Workflow

### Update configuration

- Add or edit sources in `config/sources.yaml`
- Keep `parser_type: generic` unless the site clearly needs a bespoke parser
- Set each source timezone correctly because date filtering uses the source timezone

### Run and inspect

- Prefer `--run-once` during debugging
- Inspect generated Excel files under `output/`
- If Feishu is enabled, verify webhook-related settings in `.env`

### Extend parsing

- Use `app/crawlers/generic_news_parser.py` for conservative improvements that help many static news sites
- Add site-specific logic in `app/crawlers/custom_parsers.py`
- Register new parser types in `app/crawlers/factory.py`

### Validate before finishing

- Run `pytest`
- If code paths changed around scheduling or CLI behavior, also run:

```bash
.venv/bin/python -m app.main --help
```

## Important Behavior

- Date filtering is based on each source's timezone and keeps yesterday + today
- Items with unresolved publish dates are still included with empty `published_at`
- Labels are assigned by keyword rules in `app/tagging.py`
- Feishu v1 sends text summaries; file sending is only a reserved interface

中文说明：
- 时间范围按每个来源网站自己的时区计算“昨天和今天”
- 发布时间解析失败的文章也会保留，只是时间列留空
- 标签目前走关键词规则
- 飞书当前以文本通知为主

## When To Prefer A Custom Parser

Write a custom parser when:
- the site requires JavaScript rendering
- the publish date is hidden in unusual embedded data
- the list page structure is too irregular for the generic parser
- the site needs special headers, auth, or anti-bot handling
