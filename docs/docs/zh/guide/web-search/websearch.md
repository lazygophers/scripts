# websearch

多引擎网页检索（全引擎免 key 并行，按 URL 合并去重）

## 用法

用法：`websearch [-h] [-n LIMIT] [-p PAGE] [--engine {ddg,ddg-lite,bing,google,yandex,sogou,baidu,360,arxiv,crossref,pubmed,searx,wikipedia,github}] [-f {plain,json,tsv,csv,table}] [--json] [--timeout TIMEOUT] query [query ...]`

**位置参数**

| 位置参数 | 说明 |
| :--- | :--- |
| `query` | 搜索词(多词直接跟在后面) |

**选项**

| 选项 | 说明 |
| :--- | :--- |
| `-h, --help` | show this help message and exit |
| `-n, --limit LIMIT` | 每个引擎抓几条(默认 20;合并去重后可能少于引擎总数) |
| `-p, --page PAGE` | 取第几页(默认 1;偏移 = (page-1)×limit) |
| `--engine {ddg,ddg-lite,bing,google,yandex,sogou,baidu,360,arxiv,crossref,pubmed,searx,wikipedia,github}` | 只用指定引擎(默认全部引擎) |
| `-f, --format {plain,json,tsv,csv,table}` | 输出格式(默认 plain;tsv/csv 适合管道,table 用 Rich 表格) |
| `--json` | 等价 --format json(管道给 jq 用) |
| `--timeout TIMEOUT` | 单引擎超时秒数(默认 15) |

**子命令**

```bash
websearch engines                  列出全部引擎(* = 默认启用)
websearch set engines <名称...>    改默认引擎集(写配置文件)
websearch set engines --reset      恢复内置默认引擎集
```

## 示例

```bash
websearch rust async
```
