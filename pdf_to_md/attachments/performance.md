# 性能工程与代码减量（2025-08 实测）

对应 pdf-to-md-plan.md「性能」条目。约束：不影响产物（MD 哈希全程 `467d3d59…` 不变）。

## 基线（16s）

sh 全程 16s——PDF 全量解析 ×2（pipeline/verify 各一次）、`_find_captions` 全 doc dict 扫描 ×2（lint 跑两遍）、附录图幂等检查前先全 doc 扫描。

## 改造

1. `run_pipeline.py --verify`：verify 进程内直调并复用 pages，sh 删除独立 verify 步骤；
2. `extract_book` 磁盘缓存 `.cache/pages.pkl`：键 = PDF(mtime,size) + manifest 的 `figures[].page/clip` **内容摘要**。
   - 坑：不能用 manifest mtime——`enrich_manifest` 每次运行都改写 manifest，会把缓存永久打穿；真正影响提取的只有 clips 字段。也不能放 `MANIFEST_PATH.parent`（那是 extracted_images/）。
   - 缓存损坏自动回退全量提取；写入 best-effort 失败不影响结果。
3. `_find_captions` 加 `search_for("Figure E.")` 页级预筛（C 侧搜索替代逐页 dict 解析，2.2s→<0.3s，结果一致）；`_render_appendix_figures` 两级幂等：E.1–E.5 PNG 齐全时由文件名重建映射直接返回；
4. `--verify` 时跳过 pipeline 的信息性 lint（verify 第 6 步做权威校验）。

**效果**：冷 16s→7.3s、热 2.0s（8×）。

## 减量实证（A/B 字节比对护栏）

- 已删：零引用的 `mdlib/cli.py`；旧链遗迹 pass `concept_boxes_to_blockquote`（禁用后输出逐字节一致）。
- patches 内容重建文案外置 `pipeline/patches_data.json`（锚点逻辑与文案分离，修文案不动代码）。
- **谨慎区 A/B 证伪三项候选删除**：`dehyphenate_text` 行级层、`fix_hyphenation`、`dedupe_figures` 各删任一输出即变——全部承重，保留。看似冗余的双层断词是新旧链等价性的必要组成。
- 未做：迁移 `audit_pdf_md.py`/`check_line_integrity.py` 出管道包——golden/diff_triage 多处引用现路径，纯搬家零功能收益。

## 方法论

任何"疑似死代码"删除前必须 A/B：monkeypatch 目标函数为 identity → 全量渲染 → md5 对比产物。哈希不变才可删。
