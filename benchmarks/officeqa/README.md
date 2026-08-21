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
| Full 数据文件 | `officeqa_full.csv`，154868 bytes，Git blob `b9edb082f3143783634b5efc8c6258055a281b1e` | 文件本体 gated；当前仓库不分发 |
| 官方评分代码 | `databricks/officeqa@7b9a3c154ef9fb40215bb67934afc43e6799de16:reward.py` | SHA-256 `0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f` |
| 数值容差 | `0.0` | 上述 `score_answer()` 的默认值；每次运行仍须显式记录 |
| UID 来源 | `microsoft/SkillOpt@da06b157cb9878e378663ee1ecf429c83fe1a8f9:data/officeqa_id_split` | 仅用于公开 UID/difficulty 清单 |

CSV 的 Git blob OID 不是内容 SHA-256。获得 gated access 后，运行者必须计算本地 `officeqa_full.csv` 的 SHA-256 并写入运行元数据；在真正取得文件前，不应猜测或复制未知校验值。

## 获取官方数据

先在 [databricks/officeqa](https://huggingface.co/datasets/databricks/officeqa) 申请访问，并遵守数据集页面的用途约束。随后使用固定 revision 下载：

```bash
python -m pip install huggingface_hub
hf auth login

python - <<'PY'
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    repo_id="databricks/officeqa",
    repo_type="dataset",
    filename="officeqa_full.csv",
    revision="8ecbf18d3833daf4750a903d14963e4c4c1d4cd8",
)
print(path)
PY
```

对输出路径执行 `sha256sum <path>`（Windows 可用 `Get-FileHash <path> -Algorithm SHA256`），并确认 CSV 为 246 行样本、UID 唯一、difficulty 为 113 个 `easy` 和 133 个 `hard`。不要把 CSV、问题、答案或完整模型轨迹提交到 Git。

## 使用官方评分器

评分必须从固定 commit 加载官方 `reward.py`，不能在 xskill 中复制一份并逐渐产生语义分叉：

```bash
git clone https://github.com/databricks/officeqa.git
cd officeqa
git checkout 7b9a3c154ef9fb40215bb67934afc43e6799de16
sha256sum reward.py
```

每条结果至少记录 scorer commit、`reward.py` SHA-256 和显式 tolerance。未来 runner 应通过文件路径加载该评分器，并拒绝校验和不匹配的版本。

## Full manifest

[`manifests/officeqa_full.json`](manifests/officeqa_full.json) 只包含 UID 和 difficulty，并记录完整来源链。它可以用来检查 gated CSV 是否缺题、重复或混入 Pro V2，但本身不能执行评测，也不能还原问题和答案。

一次可发布的 Full 结果还必须记录：

- xskill、SkillOpt、skill、harness 和 scorer 的精确 commit；
- 模型完整标识、prompt/config/seed、并发、超时、重试和缓存策略；
- 每个 UID 的终态、请求次数、输入/输出/缓存 token、费用与延迟；
- gated CSV SHA-256、manifest SHA-256 和原始结果 SHA-256；
- `pass`、`fail`、`invalid`、`timeout`、`infra_error`、`skipped` 的明确分类。

## 后续阶段

本阶段解决来源固定、Full UID 清单和 README 口径问题。可续跑 runner、结果 schema、去重聚合器及其无网络单元测试应作为下一独立 PR；真实 OfficeQA Full 运行和成本摘要必须在取得数据授权、模型凭据并确认预算后单独发布。
