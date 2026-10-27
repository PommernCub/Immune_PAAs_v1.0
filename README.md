# Immune_PAAs_v1.0

项目用于整理 TLR4/DC 相关短肽文献，为后续人工审核、数据抓取和模型研究提供可追溯的数据接口。当前实现只覆盖**模块一：文献检索器**；它检索文献元信息，不下载全文、补充材料或论文数据，也不判断生物学有效性。

## 目录与中间协议

- `src/immune_paa/literature_searcher.py`：可配置的检索器与命令行入口。
- `config/search_config.example.json`：检索配置模板。请复制后填写关键词、数据库和范围，不要把未确定的关键词写进程序。
- `examples/mock_search_config.json`：离线测试配置。
- `tests/`：不访问网络的去重和来源保留测试。
- `results/*.jsonl`：每行一篇去重后的文献元信息，作为后续模块输入。
- `results/*_stats.json`：检索统计和失败记录。

JSONL 文献记录使用 `literature-record/v1` 的字段约定（当前记录包含 `record_id`、标题、作者、期刊、日期、DOI/PMID、摘要、文献类型、`database`、`keyword_groups`、`search_queries`、`retrieved_at` 和 `deduplication_keys`）。后续数据抓取器应保留 `record_id`，并在自己的记录中增加原文文件、页码/表格/图号、上下文和人工审核状态；原始检索记录不得覆盖。

## 安装与测试

项目只使用 Python 标准库。建议使用 Python 3.11+：

```bash
python -m venv .venv
. .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 运行检索器

```bash
cp config/search_config.example.json config/search_config.json
# 编辑 config/search_config.json，填入用户确认的检索式和范围
PYTHONPATH=src python -m immune_paa.literature_searcher \
  --config config/search_config.json \
  --results results/literature.jsonl \
  --stats results/literature_stats.json
```

PubMed 请求使用 NCBI E-utilities；代码只请求 ESearch/ESummary 元信息。网络错误、未知数据库和接口异常会写入统计文件的 `errors`，并使命令以非零状态结束。没有错误时退出码为 0。

## 人工审核边界

检索统计不能自动选出“核心文献”。请人工筛选并下载核心文献，再交给后续模块。当前没有预设关键词、影响因子、阈值或 P+/P?/N-/U 规则；这些内容必须在配置或专家规则中明确后才能用于后续处理。不同指标（如 Kd、EC50、模拟能量和细胞因子）也不会在本模块中合并。

## 待确认项目

- 关键词组名称、检索式、数据库和发表时间范围；
- 是否保存摘要、期刊范围及其他数据库；
- 去重优先级（DOI、PMID、标题）和数据库访问限制；
- 后续模块的字段词表、人工审核流程及专家分类规则。
