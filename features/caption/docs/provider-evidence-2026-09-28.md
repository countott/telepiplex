# Telepiplex Caption 字幕来源验证

验证日期：2026-09-28。这里只处理外挂字幕。网络可用性是当天实测，不代表来源长期在线；电影身份由明确的测试输入给出，没有将示例身份冒充元数据服务的检索结果。

## 已实现的来源与边界

| 来源 | 接入方式 | 本次实际验证 | 后续配置 |
| --- | --- | --- | --- |
| ASSRT / 射手网（伪） | 默认使用普通公开搜索页与其直接公开的下载链接；配置 Token 后走官方 API | 无 Token 搜索、真实 ZIP/SRT 下载及正文检查通过；追加验证已下载的真实 RAR 可解包并正确排列简繁单语字幕 | 个人 Token 可选；公开来源已有成功样本，不保证每份上传均可下载 |
| Shooter / 原射手哈希接口 | 本地视频四段 MD5 哈希检索 | 端点可访问；空查询返回其无结果标记 `FF`。没有合适的真实视频哈希供本次命中测试 | 需要本地视频，不适用于单独片名查询 |
| SubHD | 公开搜索页面；仅跟随详情页明确公开的字幕文件链接 | 搜索返回真实字幕、版本及语言信息；当前详情页的动态下载准备需要在网站完成，明确返回 `user_action_required` | 不猜测私有下载接口、不绕过验证码；当前自动下载不可用，可在来源网站手动获取 |
| 字幕库 / Zimuku | 保留来源状态与来源网站链接 | 主域名及官方列出的备用域名搜索请求均返回 404；当前 `robots.txt` 为 `Disallow: /`，自动检索停在 `user_action_required` | 待网站提供允许使用的接口后扩展；当前自动检索不可用，可在来源网站手动获取 |
| OpenSubtitles.com | 官方 REST API，限定 `zh-cn`、`zh-tw`、`ze`、`zh-ca`；排除来源标记的 AI / 机器翻译 | 公共语言接口证实四种中文分类；未配置 API Key 的字幕检索返回 403；适配器预先显示 `auth_required` | 后续补个人 API Key，用户名/密码或 Token 可用于账户下载配额 |

所有来源均保留失败状态，不把授权缺失、验证码、网页变更或网络失败写成“没有字幕”。ASSRT 配置了 Token 但授权失败时也不会静默掩盖为无结果。下载地址按需获取，API Token 不放进 URL，不传递给跨主机下载重定向，不写入诊断。来源页面或文件名中的 CMCT、DAA 等名称仅用于辨认此次样本，没有单独验证对应字幕组的官方服务是否可用。

## 真实字幕质量冒烟验证

使用完整 `CaptionEngine.retrieve`，开启默认五个来源，没有 API Key、没有视频文件。候选需要通过年份/标题匹配、受限解压、实际对白/时间码解析、简繁识别与双语检测之后，才能按用户优先级参与选择。

| 输入身份 | 实际选中 | 正文验证结果 |
| --- | --- | --- |
| Interstellar / 星际穿越，2014，原始语言英语 | [ASSRT 698567](https://assrt.net/xml/sub/698/698567.xml)，DAA 版本简体及英文 ASS | `chi`、双语、2,205 个有效对白段 |
| 流浪地球 / The Wandering Earth，2019，原始语言中文 | [ASSRT 632031](https://assrt.net/xml/sub/632/632031.xml)，STUTTERSHIT 简体 SRT | `chi`、单语、1,797 个有效对白段 |
| 你的名字 / Your Name，2016，原始语言日语 | [ASSRT 617491](https://assrt.net/xml/sub/617/617491.xml)，CMCT 简体 ASS | `chi`、单语、1,466 个有效对白段 |

这轮三例于 2026-09-28 03:12–03:14 UTC 完成，统计见 [机器可读验证记录](provider-validation-2026-09-28.json) 的 `live_results`。没有视频，因此 `timing_verified=false`，明确携带 `video_timing_unverified`；它们验证了这些样本的格式、对白结构、语言分类及选择次序，不证明翻译准确性或与任意视频版本逐句同步。《星际穿越》和《你的名字》触及候选检查上限，所选结果仅是实际检查候选中的最优项。记录中的来源 `status=ok` 是当时的搜索状态；同轮 SubHD 下载仍要求网站操作，不能把这个历史字段解释为下载成功。早一轮还验证了 [Interstellar CMCT 612770](https://assrt.net/xml/sub/612/612770.xml)，简中双语 ASS 为 2,015 个有效对白段。CMCT 和 DAA 包中的英文单语字幕都有 `insufficient_chinese_dialogue` 拒绝记录，不会因为网站标注“双语”就被误用。

早一轮还测试了《千与千寻》（2001，日语）：当时完整引擎没有选中，保留为失败记录。其实际下载暴露出合法文件名时间 `02:04:32` 被过严路径规则拦截、未提取的大体积 `.sub` 附件误触单字幕限制，以及本地 rarfile／bsdtar 参数顺序不兼容；`.zip` 下载名对应的实际内容为 RAR，解包依据内容魔数识别。

修复后，直接复查先前已下载的 [ASSRT 612651](https://assrt.net/xml/sub/612/612651.xml) 压缩包（11,153,340 字节），未重新联网，也未重新执行完整引擎检索。Mac `bsdtar` 受限输出分支成功提取 3 份 SRT 并通过成员长度／CRC 检查，质量与排名结果如下；原始压缩包 SHA-256、各成员名称与结果记录在 JSON 的 `artifact_rechecks`，与完整引擎结果分开保存。

| RAR 内实际成员 | 正文结果 | 日语原始语言下的选择结果 |
| --- | --- | --- |
| Mandarin Simplified Chinese.srt | `chi`、单语、1,416 个有效对白段 | 通过，优先级 200 |
| Mandarin Traditional Chinese.srt | `cht`、单语、1,416 个有效对白段 | 通过，优先级 100，排在简中之后 |
| English.srt | 1,092 个有效对白段 | 拒绝：`insufficient_chinese_dialogue`、`chinese_script_unverified` |

这次追加复查只证实真实 RAR 的提取、内容检查和包内排名，没有将原先失败的《千与千寻》完整检索改写成成功。三份结果均为 `timing_verified=false`；未验证视频版本、播放同步、Search 元数据解析或 115／Rename 写入，容器 `unar` 分支也不是此次 Mac 实测后端。

日语用例还暴露出 ASSRT 默认排序会让近期弱相关上传占据第一页。适配器改用网站公开的相关度排序，并把已知电影年份加入检索条件；对于非英语作品优先中文译名。这使《你的名字》从第一页没有正确电影候选，变为可通过正文检查的实际结果。来源语言标签仅帮助安排有限的下载检查顺序，最终优先级仍以正文检测为准。

完整字幕正文和下载压缩包没有写入项目目录或文档；临时实测资料只存于 `/tmp`。本文件只保留统计信息与公开来源详情页。

## 可重复的本地验证

```bash
cd /Users/young/Documents/telepiplex/features/caption
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_providers.py
```

本次实际结果：**33 passed**。覆盖公开 HTML 解析、API 新鲜下载链接、文件 ID、剧集所属作品身份、授权与限流、简繁/双语来源提示、哈希适用性、网页验证码、非预期网页、大小限制、私网地址、跨主机凭据隔离和中文响应文件名修复。网络冒烟为独立实测，没有用这些离线契约测试替代生产来源验证。

## 一手资料

- [ASSRT 官方 API 文档](https://assrt.net/api/doc)：Token、检索/详情端点、配额及临时下载地址；应用保留“字幕服务由 assrt.net 提供”的来源说明。
- [ASSRT 公开搜索](https://assrt.net/sub/?searchword=Interstellar) 和 [CMCT 字幕详情](https://assrt.net/xml/sub/612/612770.xml)：本次可直接取得的公开字幕样本。
- [SubHD 公开搜索](https://subhd.tv/search/Interstellar)、[实际详情](https://subhd.tv/a/Cm0tsS) 与 [robots.txt](https://subhd.tv/robots.txt)：允许公开页面，下载/API 路径另有约束。
- [字幕库官方页面](https://zimuku.org/subs/29962.html)：列出 `zimuku.org`、`srtku.com`、`zmk.pw`；[当前访问政策](https://zimuku.org/robots.txt)。
- [Shooter 哈希端点](https://www.shooter.cn/api/subapi.php)：本次直接请求验证，未声明真实片源命中。
- [OpenSubtitles 中文分类接口](https://api.opensubtitles.com/api/v1/infos/languages)；[官方 REST 文档入口](https://opensubtitles.stoplight.io/docs/opensubtitles-api)；[官方旧 API 退役说明](https://forum.opensubtitles.com/t/opensubtitles-org-api-final-shutdown-notice-for-non-vip-users/5045)。本版只接入 `.com` REST，不依赖退役的 XML-RPC。
