# OfficeQA Full 评测来源与复现边界

本目录只面向 OfficeQA Full，不把 OfficeQA Pro V2 的结果混入同一口径。这里不提交受控问题、答案或语料，只保存公开 UID、版本信息和校验元数据。

## 基准口径

Databricks 当前把 OfficeQA 定义为三个基准：OfficeQA Pro 有 133 道 hard 题；OfficeQA Full 有 246 题，是 Pro 加 113 道 easy 题；OfficeQA Pro V2 有 90 题，并使用另一套文档语料。OfficeQA 官方仓库没有定义“1/4 子集”，Pro 和 Full 在 Hugging Face 上各自以单个 `train` split 发布。

xskill README 中的 60.47% 是历史下游子集结果。仓库没有保留该次运行的 UID、抽样代码、配置或原始输出，无法证明它等于任何上游 split，也不能从分数反推出样本。因此本次先纠正“官方 1/4”的表述并保留历史数值，不为缺失产物补造 manifest。

Microsoft SkillOpt 另行发布了基于 OfficeQA Full 的 ID-only manifest，train/val/test 为 50/24/172。它的三部分并集恰好覆盖 246 个唯一 UID（113 easy、133 hard），但这是 SkillOpt 的下游划分，不是 OfficeQA 官方 split。本目录的 `officeqa_full.json` 仅借它公开的 UID 和 difficulty 构造无 split 的 Full 清单，不把 SkillOpt 的 train/val/test 语义带入官方口径。

## 固定版本

| 工件 | 固定值 | 核验说明 |
|---|---|---|
| OfficeQA 数据集 | `databricks/officeqa@8ecbf18d3833daf4750a903d14963e4c4c1d4cd8` | SkillOpt manifest 固定的 revision，HF API 可核验 |
| Full 数据文件 | `officeqa_full.csv`，154868 bytes，Git blob `b9edb082f3143783634b5efc8c6258055a281b1e` | gated 文件不入库；授权下载 SHA-256 为 `b0b270d15acdd04dcdc6ca389f089010ffe2b8453dbb400343229ea73b66c6d7` |
| Full 文档语料 | `treasury_bulletins_parsed/transformed` 根目录下 697 个 TXT，共 383162413 bytes | 按文件名排序后，对 `UTF-8 文件名 + NUL + 小写文件 SHA-256 + LF` 串联值计算的树 SHA-256 为 `851bfc5dbf2fc42abb1cc5aa4a4b5de872cf1f3b473d8cf6dd8f7c637d0c7d24` |
| 官方评分代码 | `databricks/officeqa@7b9a3c154ef9fb40215bb67934afc43e6799de16:reward.py` | SHA-256 `0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f` |
| 数值容差 | `0.0` | 上述 `score_answer()` 的默认值；每次运行仍须显式记录 |
| UID 来源 | `microsoft/SkillOpt@da06b157cb9878e378663ee1ecf429c83fe1a8f9:data/officeqa_id_split` | 仅用于公开 UID/difficulty 清单 |

CSV 的 Git blob OID 不是内容 SHA-256。运行者仍应重新计算本地 `officeqa_full.csv` 的 SHA-256，并在读取问题或答案前拒绝版本不符的文件。

## 获取官方数据

先在 [databricks/officeqa](https://huggingface.co/datasets/databricks/officeqa) 申请访问并运行 `hf auth login`，随后把 gated 数据下载到仓库之外的本机缓存：

```bash
hf auth login
export OFFICEQA_REVISION=8ecbf18d3833daf4750a903d14963e4c4c1d4cd8
export OFFICEQA_DATA_DIR="$HOME/.cache/xskill/officeqa/$OFFICEQA_REVISION"
hf download databricks/officeqa officeqa_full.csv README.md --repo-type dataset --revision "$OFFICEQA_REVISION" --local-dir "$OFFICEQA_DATA_DIR"
hf download databricks/officeqa --repo-type dataset --revision "$OFFICEQA_REVISION" --include 'treasury_bulletins_parsed/transformed/*.txt' --exclude 'treasury_bulletins_parsed/transformed/*.zip' --local-dir "$OFFICEQA_DATA_DIR"
```

固定 revision 的 `treasury_bulletins_transformed.zip` 只有 696 个 TXT，并缺少 Full CSV 引用的 `treasury_bulletin_2025_09.txt`；因此复现命令直接下载根目录下 697 个独立 TXT。不要把 ZIP 解压产物或 `__MACOSX` 目录混进该目录，因为递归检索会把额外的 `.txt` 也带入模型工作区。对 CSV 执行 `sha256sum`（Windows 可用 `Get-FileHash -Algorithm SHA256`），并确认 CSV 哈希、完整语料树哈希、246 个唯一 UID、113 个 `easy`、133 个 `hard` 以及全部引用文件都符合 manifest。不要把 CSV、问题、答案、语料、逐题预测或完整模型轨迹提交到 Git。

## 使用官方评分器

[`scripts/bench/officeqa/vendor/reward.py`](../../scripts/bench/officeqa/vendor/reward.py) 是固定 commit 的原样副本，并附带上游 `LICENSE-APACHE`、`NOTICE` 和来源说明。校验工具会在加载评分器之前验证 SHA-256，任何本地修改都会立即失败；升级评分器必须固定新的上游 commit、更新哈希并重新审查语义。

## 校验输入和聚合结果

后续 runner 生成的 JSONL 结果可以独立校验和聚合，命令不会调用模型，也不会访问网络：

```bash
python -m scripts.bench.officeqa.evaluate --csv "$OFFICEQA_DATA_DIR/officeqa_full.csv" --corpus-dir "$OFFICEQA_DATA_DIR/treasury_bulletins_parsed/transformed" --manifest benchmarks/officeqa/manifests/officeqa_full.json --results "$OFFICEQA_OUTPUT_DIR/results.jsonl" --output "$OFFICEQA_OUTPUT_DIR/summary.json"
```

校验器会验证 gated CSV、公开 UID、难度分布、引用语料、评分器哈希和逐题结果 schema，并用固定评分器重新计算所有已有预测。只有 manifest 中的 246 个 UID 全部具有可评分预测时才会产生 `official_full_accuracy`；子集、不完整结果以及含 `invalid`、`timeout`、`infra_error` 或 `skipped` 的结果都不会被标记为官方 Full 成绩。

## Full manifest

[`manifests/officeqa_full.json`](manifests/officeqa_full.json) 只包含 UID 和 difficulty，并记录完整来源链。它可以用来检查 gated CSV 是否缺题、重复或混入 Pro V2，但本身不能执行评测，也不能还原问题和答案。

一次可发布的 Full 结果应记录：

- xskill、SkillOpt、skill、harness 和 scorer 的精确 commit；
- 模型完整标识、prompt/config/seed、并发、超时、重试和缓存策略；
- 每个 UID 的终态、请求次数、输入/输出/缓存 token、延迟，以及计费 endpoint 可提供的费用；
- gated CSV SHA-256、manifest SHA-256 和原始结果 SHA-256；
- `pass`、`fail`、`invalid`、`timeout`、`infra_error`、`skipped` 的明确分类。

## 后续阶段

`run.json`、`results.jsonl`、`summary.json` 和 spill 文件都必须留在仓库外；对外只分享经过复核且不包含 gated 问题、答案、预测或私有 endpoint 的聚合摘要。

通用可续跑 runner 和真实 OfficeQA Full 运行结果仍应由后续独立 PR 提交。
