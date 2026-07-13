# media-search 元数据回查能力实施计划

1. 用失败测试锁定只解析、不下载的能力行为。
2. 复用现有 `build_confirmable_search_plan` 与 `confirm_media_metadata` 输出 canonical contract。
3. 在 manifest 注册 `media.search`，运行全量测试及 manifest 校验。
