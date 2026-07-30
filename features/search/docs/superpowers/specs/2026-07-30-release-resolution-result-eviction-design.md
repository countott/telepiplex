# search 片源解析失败结果淘汰设计

## 目标

用户从 Prowlarr 结果中选择一项后，如果 search 无法从该结果取得有效
magnet，则只淘汰该结果并让用户改选其余结果，不把整个 search plan
标记为失败。

## 行为合同

- 只处理用户已经选择的 `release_id`。
- resolver 抛出异常，或返回值不是 `magnet:?`，都视为该结果无法取得
  下载内容。
- 失败结果从 `stored["results"]` 和 `stored["release_by_id"]` 中移除。
- 已确认的 `media_metadata`、保存目录、其他片源及其顺序保持不变。
- 仍有结果时，原消息更新为剩余结果列表，并提示失败项已移除。
- 已无结果时，显示当前结果均无法取得下载内容，只保留退出入口。
- 旧按钮再次引用已移除的 `release_id` 时，继续按无效片源处理。
- 不自动重新执行 Prowlarr 搜索，不自动选择下一项，不修改 download 的
  独立 `/m` 入口。

## 状态

解析失败后 operation 回到：

- `state=awaiting_input`
- `stage=release_selection`
- `control=exit`

`selection_frozen` 恢复为 `False`，并清除 `selected_release_id`。成功解析和
既有 download handoff 流程保持不变。

## 日志

记录结构化事件：

```text
search_release_resolution status=removed release_id=<id> error=<kind>
```

日志不包含下载 URL、magnet、Cookie 或 Header。

## 验收

- 两个结果中第一个解析失败后，只展示第二个结果。
- 失败结果从当前映射中移除，旧 callback 不再有效。
- 最后一个结果解析失败后显示无可用内容和退出按钮。
- 成功解析仍携带原 canonical contract 调用 `download.provider`。

