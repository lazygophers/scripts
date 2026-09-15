# Python 命令行 mail 工具（收发邮件、多账号）2026 年可行性调研

> 调研日期：2026-09-10
> 结论口径：每条都带出处；查不到的标 `推测:` / `需要:`，并写清试过什么。

---

## 一、结论先行

**能做。** 用 Python 标准库 `imaplib` + `smtplib` + `email` 就能写出一个多账号的收发邮件 CLI，不需要任何第三方依赖。真正的难点不在 Python，在**认证**。

**最大的三个坑：**

1. **「邮箱地址 + 登录密码」这条路，在国际大厂已经全线关死。**
   - Gmail / Google Workspace：2025-03-14 起，IMAP/POP/SMTP + 普通密码全部失效（<https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth>）。剩下两条路：**应用专用密码（App Password，需先开二步验证）** 或 **OAuth2**。
   - Outlook.com / Hotmail / Live 个人账号：**2024-09-16 起 Basic Auth 彻底不可用**，只能 OAuth2（<https://support.microsoft.com/en-us/office/modern-authentication-methods-now-needed-to-continue-syncing-outlook-email-in-non-microsoft-email-apps-c5d65390-9676-4763-b41f-d7986499a90d>）。
   - Microsoft 365 / Exchange Online 企业账号：IMAP/POP 的 Basic Auth 早在 2022-10-01 就关了，且**任何人（包括微软支持）都无法重新打开**（<https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online>）。SMTP AUTH 是最后的例外，2026 年底才默认关闭。
   - 国内 QQ / 163 走的是「授权码」，本质还是密码认证，**目前仍然可用**，但需要先在网页端手动开启服务、手动生成授权码——CLI 没法自动化这一步。

2. **一旦要接 OAuth2，工具的复杂度会翻倍，而且需要「注册一个 app」这种非技术性前置工作。**
   OAuth2 CLI 需要：注册 client（Google Cloud Console / Microsoft Entra）→ 本地起 HTTP 监听器接回调（`http://127.0.0.1:<port>`）→ 换 refresh_token → 每次用 refresh_token 换 access_token → 用 SASL XOAUTH2 塞进 IMAP `AUTHENTICATE` / SMTP `AUTH`。
   Google 明确说 installed app **无法保密 client_secret**（<https://developers.google.com/identity/protocols/oauth2/native-app>，"the client cannot keep the `client_secret` confidential"），所以 client_secret 只是形式。Thunderbird 的 client_id/secret 是公开写在源码里的，技术上可以复用（见第六节），但那属于冒用他人身份，不建议。

3. **`imaplib` 给的是「协议原语」，不是「邮件对象」。**
   `imaplib` 返回的是 `(type, [bytes 或 tuple, ...])` 这种半解析结构（<https://docs.python.org/3/library/imaplib.html>："Each *data* is either a `bytes`, or a tuple. If a tuple, then the first part is the header of the response, and the second part contains the data"）。FLAGS 有 `ParseFlags()` 帮你，但 **UID / BODYSTRUCTURE / 带引号的文件夹名 / 各种 literal，都得自己写解析**。这是最容易写出 bug 的地方，也是第三方库唯一真正值钱的地方。

**推荐路径（懒人版）：**
先只支持「应用专用密码 / 授权码」这一类，用纯标准库写。Gmail 用 App Password（仍然可用），iCloud / Fastmail / Zoho / QQ / 163 天然就是这个模式。**只有 Outlook.com 个人账号必须 OAuth2**——先不支持它，等真的需要了再加。这样第一版零依赖、几百行搞定。

---

## 二、各服务商认证现状表

| 服务商 | 邮箱+登录密码直连 IMAP/SMTP | 应用专用密码 / 授权码 | 强制 OAuth2？ | 关键日期与出处 |
|---|---|---|---|---|
| **Gmail 个人账号** | ❌ 不可以 | ✅ App Password（**必须先开二步验证**） | 否（App Password 仍可用），但 Google 官方明确「不建议」 | 2025-03-14 关闭 LSA（[Workspace 知识库](https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth)）；2025-01 起 IMAP 开关取消、IMAP **常开**（[Gmail 帮助 7126229](https://support.google.com/mail/answer/7126229)） |
| **Google Workspace 管理账号** | ❌ 同上，规则一致 | ✅ 但**管理员可以禁用**；若二步验证只配了安全密钥，App Password 选项会消失 | 否 | 同上；App Password 规则见 [support.google.com/accounts/answer/185833](https://support.google.com/accounts/answer/185833) |
| **Outlook.com / Hotmail / Live 个人** | ❌ **2024-09-16 起彻底失效** | ❌ 官方论坛口径：9/16 后 IMAP/POP 的所有认证方式和密码都失效 | ✅ **是**，只能 Modern Auth / OAuth2 | 2024-09-16（[MS 支持 c5d65390](https://support.microsoft.com/en-us/office/modern-authentication-methods-now-needed-to-continue-syncing-outlook-email-in-non-microsoft-email-apps-c5d65390-9676-4763-b41f-d7986499a90d)，原文："After September 16th, users attempting to connect their Microsoft accounts through Basic Authentication will fail to do so."） |
| **Microsoft 365 / Exchange Online 企业** | ❌ IMAP/POP 已关且**不可恢复**；SMTP AUTH 尚可 | ❌（Basic Auth 关闭同时禁掉了 app password） | ✅ IMAP/POP 必须 OAuth2 | IMAP/POP：2022-10-01（21Vianet 是 2023-03-31）；**SMTP AUTH：2026 年 12 月底对存量租户默认关闭，2027 下半年公布最终移除日**（[Exchange 团队博客 4489835](https://techcommunity.microsoft.com/blog/exchange/updated-exchange-online-smtp-auth-basic-authentication-deprecation-timeline/4489835)，发布 2026-01-27，v3.0 更新于 2026-01-29） |
| **iCloud Mail** | ❌ | ✅ **必须** app-specific password | 否 | [Apple 支持 102525](https://support.apple.com/en-us/102525)（更新日期 2026-02-03）原文："Password: Generate an app-specific password." |
| **QQ 邮箱** | ❌ | ✅ **必须**用 16 位授权码 | 否 | [help.mail.qq.com/detail/106/985](https://help.mail.qq.com/detail/106/985) 原文："授权码是QQ邮箱推出的，用于登录第三方客户端的专用密码"；需先在【设置】-【账号与安全】-【安全设置】点「开启服务」再「生成授权码」。**改账号密码会导致授权码过期** |
| **163 / 126 / yeah.net** | ❌ | ✅ **必须**「客户端授权密码」 | 否 | [help.mail.163.com 新增授权码](https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac286624f309a1a7089) 原文："授权码在开启后网页上只出现一次，请及时保存"；开启路径：网页版 →「设置」→「POP3/SMTP/IMAP」（[什么是POP3、SMTP及IMAP](https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac25ef2e192b234ae4d)），需短信验证 |
| **Fastmail** | ❌ | ✅ **必须** app password | 否（也支持 OAuth，Thunderbird 用的就是） | [fastmail.help 1500000278342](https://www.fastmail.help/hc/en-us/articles/1500000278342-Server-names-and-ports) 原文："You will need to get an app password to connect to these servers. You cannot use your regular Fastmail password." ⚠️ **Basic 套餐根本没有 IMAP/SMTP 权限** |
| **Zoho Mail** | ✅ 未开 2FA 时可用账号密码 | ✅ 开了 2FA 就**必须** application-specific password | 否 | [zoho.com/mail/help/imap-access.html](https://www.zoho.com/mail/help/imap-access.html) |
| **Proton Mail** | ❌ **根本不支持标准 IMAP/SMTP** | — | — | 必须装 **Proton Mail Bridge**（本机跑一个代理，把 IMAP/SMTP 翻译成 Proton 的加密协议），且 **Bridge 只对付费套餐开放**，只有桌面版（macOS/Windows/Linux），没有移动端，不支持 POP3。出处：<https://proton.me/support/imap-smtp-and-pop3-setup> |

> **一句话解释「应用专用密码 / 授权码」**：不是你的登录密码，而是邮箱服务商额外发给你的一串一次性字符串，专门给第三方软件用；泄露了不影响主账号，随时能在网页端作废。

---

## 三、服务器地址与端口表

| 服务商 | IMAP | SMTP | POP | 加密 | 出处 |
|---|---|---|---|---|---|
| **Gmail** | `imap.gmail.com:993` | `smtp.gmail.com:465` (SSL) 或 `:587` (STARTTLS) | `pop.gmail.com:995` | IMAP/POP 强制 SSL | [developers.google.com/gmail/imap/imap-smtp](https://developers.google.com/gmail/imap/imap-smtp)（页面末尾标注最后更新 2026-04-23）原文："对 `imap.gmail.com:993` 处的 IMAP 服务器和 `pop.gmail.com:995` 处的 POP 服务器的入站连接需要 SSL…使用端口 `465`（对于 SSL）或端口 `587`（对于 TLS）" |
| **Outlook.com** | `outlook.office365.com:993` | `smtp-mail.outlook.com:587` | `outlook.office365.com:995` | IMAP/POP SSL/TLS；SMTP STARTTLS | [MS 支持 d088b986](https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings-for-outlook-com-d088b986-291d-42b8-9564-9c414e2aa040)。⚠️ **POP & IMAP 默认是关闭的**，要去 Outlook.com 的邮件设置里手动打开 |
| **iCloud** | `imap.mail.me.com:993` | `smtp.mail.me.com:587` | 不支持 POP | SSL 必需（SMTP 报错时改 TLS/STARTTLS） | [Apple 支持 102525](https://support.apple.com/en-us/102525)。⚠️ IMAP 用户名通常只填 `@` 前面那截（`johnappleseed`），SMTP 用户名要填**完整地址** |
| **QQ 邮箱** | `imap.qq.com:993` | `smtp.qq.com:465` 或 `:587` | `pop.qq.com:995` | SSL | `需要:` 见第七节「未确认项 1」——这组数字来自 WebSearch 对腾讯官方页 `service.mail.qq.com/detail/0/427` 的摘要，我用 `webgrab`（含 `--render`）直接抓 `/detail/0/427`、`/detail/0/337`、`/detail/128/339` 三个页面都只返回侧边导航、正文为空 |
| **163 / 126 / yeah** | `imap.163.com:993`（SSL）/ `:143`（明文） | `smtp.163.com:465`（SSL，另有 994）/ `:25`（明文） | `pop.163.com:995` | SSL 推荐 | 网易官方只有两个能抓到的页面：[VIP 版 SMTP 参数](https://help.vip.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac23c588ea370a3e6bf) 明确写 "smtp.vip.163.com：非SSL协议端口号：25；SSL协议端口号：465/994"；[免费版 client.htm](https://mail.163.com/mailhelp/client.htm) 是 2007 年的老页面，只列了 `pop3.163.com` / `smtp.163.com` 的明文端口 110/25。`需要:` 见「未确认项 2」 |
| **Fastmail** | `imap.fastmail.com:993` | `smtp.fastmail.com:465`（SSL）或 `:587`（STARTTLS） | `pop.fastmail.com:995` | 全部要求 SSL/TLS | [fastmail.help 1500000278342](https://www.fastmail.help/hc/en-us/articles/1500000278342-Server-names-and-ports)。⚠️ 不要勾 SPA（Secure Password Authentication） |
| **Zoho（个人 @zohomail.com）** | `imap.zoho.com:993` | `smtp.zoho.com:465`(SSL) / `:587`(TLS) | — | SSL | [zoho.com/mail/help/imap-access.html](https://www.zoho.com/mail/help/imap-access.html) |
| **Zoho（付费组织域）** | `imappro.zoho.com:993` | `smtppro.zoho.com:465` / `:587` | — | SSL | 同上 |
| **Proton** | Bridge 本机监听 | Bridge 本机监听 | 不支持 | 见 Bridge 安装后界面 | `需要:` 见「未确认项 3」 |

> **一句话解释 SSL vs STARTTLS**：SSL（也叫 implicit TLS，端口 993/465）是「一连上就是加密的」；STARTTLS（端口 143/587）是「先明文连上，再发一条命令升级成加密」。Python 里前者用 `IMAP4_SSL` / `SMTP_SSL`，后者用 `IMAP4` / `SMTP` 再调 `.starttls()`。

---

## 四、Python 标准库能力边界（3.11+）

文档基准：docs.python.org 当前渲染的是 **3.14.7**（<https://docs.python.org/3/library/imaplib.html> 页面面包屑）。

### 4.1 有没有被废弃 / 被 PEP 594 砍掉？

**没有。** `imaplib`、`smtplib`、`poplib`、`email` **都不在 PEP 594 的删除名单里**（<https://peps.python.org/pep-0594/>）。唯一被砍的邮件相关模块是 **`smtpd`（一个 SMTP 服务端实现，Python 3.12 移除，官方建议改用 `aiosmtpd`）**——那是**收信服务器**，跟我们要写的**客户端**无关。

我 grep 了三份官方文档全文，只找到这些「deprecated」：
- `imaplib.IMAP4_SSL` / `smtplib.SMTP_SSL` 的 `keyfile` / `certfile` 参数在 **3.12 已移除**（现在只能传 `ssl_context`）——`/Users/luoxin/persons/scripts` 本次调研缓存于 `docs.python.org/3/library/imaplib.html` 第 140 行、`smtplib.html` 第 136/389 行。
- `email.header.decode_header()` / `make_header()` 被标注「仅为向后兼容而存在，新代码推荐用 `email.headerregistry.HeaderRegistry`」（<https://docs.python.org/3/library/email.header.html>）。

### 4.2 `imaplib` 有多痛？

**痛点是真的，但可控。**

| 事项 | 标准库给了什么 | 痛不痛 |
|---|---|---|
| 返回值形态 | `(type, [data, ...])`，`data` 是 `bytes` 或 `(header_bytes, literal_bytes)` 的 tuple | 😐 需要自己判断类型 |
| FLAGS 解析 | ✅ `imaplib.ParseFlags(resp)` 直接给 tuple of bytes | 🙂 白送 |
| INTERNALDATE 解析 | ✅ `imaplib.Internaldate2tuple()` / `Time2Internaldate()` | 🙂 白送 |
| **UID** | ✅ 有 `IMAP4.uid(command, arg, ...)` 方法，但**返回的响应字符串要自己正则抠 UID** | 😖 自己写 |
| **BODYSTRUCTURE** | ❌ 完全没有解析器 | 😖😖 自己写（这是最恶心的一块，嵌套括号 + 引号 + literal 混合） |
| 文件夹名（Modified UTF-7） | ❌ 没有 encode/decode 工具，中文文件夹名（如「已发送」）会是 `&XXX-` 形态 | 😖 自己写 |
| 消息序号 vs UID | 官方文档明确警告："IMAP4 message numbers change as the mailbox changes… it is highly advisable to use UIDs instead" | ⚠️ 设计时就得只用 UID |

**避坑写法**：不要碰 BODYSTRUCTURE。直接 `uid('FETCH', uid, '(RFC822)')` 把整封原始邮件拉下来，丢给 `email` 模块解析。**这样 `imaplib` 只负责「搜 UID + 拉字节」，所有解析交给 `email`**——`imaplib` 最难的那部分就绕过去了。代价是大附件邮件会全量下载。

### 4.3 中文 / 非 ASCII 邮件头（RFC 2047）

**标准库直接搞得定，而且有两种做法：**

- 老做法：`email.header.decode_header('=?utf-8?B?ZsOzbw==?=')` → `[(b'f\xc3\xb3o', 'utf-8')]`，你自己 `.decode()`（<https://docs.python.org/3/library/email.header.html>）。官方标注它「仅为向后兼容」。
- **推荐做法**：用 `email.policy.default` 解析，头部自动就是解码好的 `str`，不用手动碰 `decode_header`。

### 4.4 附件 / multipart / HTML 正文：`EmailMessage` 够不够？

**够，而且很好用。** `email.message.EmailMessage`（配 `policy=email.policy.default`）提供（<https://docs.python.org/3/library/email.message.html>）：

- `get_body(preferencelist=('related','html','plain'))` — 直接挑出「最像正文」的那一部分，不用自己遍历 multipart 树。
- `iter_attachments()` — 直接迭代附件，自动跳过被当作正文的那些 part。
- `iter_parts()` / `walk()` — 需要时再手动遍历。
- 发信侧：`set_content()` / `add_alternative()`（纯文本 + HTML 双版本）/ `add_attachment()`（会自动 `make_mixed()` 重组结构）/ `add_related()`（内嵌图片）。
- `smtplib.SMTP.send_message()` 直接吃 `EmailMessage`，不用手动 `as_string()`（<https://docs.python.org/3/library/smtplib.html>）。

⚠️ `get_body` 的三个已知坑（文档原文）：`multipart/related` 上调用会返回自身；没有 `Content-Type` 或 Content-Type 非法的 part 会被当成 `text/plain`。

### 4.5 `imaplib` 支持 XOAUTH2 吗？

**支持，但要自己拼那个 SASL 串。**

`IMAP4.authenticate(mechanism, authobject)`（<https://docs.python.org/3/library/imaplib.html>）：`authobject` 是个可调用对象，返回的 `bytes` 会被**模块自动 base64 编码**后发给服务器。所以你只要：

```python
def xoauth2(_):
    return f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode()

imap.authenticate("XOAUTH2", xoauth2)   # 注意：mechanism 要在 imap.capabilities 里出现为 AUTH=XOAUTH2
```

SASL XOAUTH2 的格式在 Google 和 Microsoft 两边**完全一致**：
`base64("user=" + userName + "^Aauth=Bearer " + accessToken + "^A^A")`，`^A` = `\x01`。
出处：[Google XOAUTH2 协议文档](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)、[Microsoft OAuth for IMAP/POP/SMTP](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)。

`smtplib` 侧同理，用 `SMTP.auth('XOAUTH2', lambda: ...)`。

### 4.6 一个必须知道的 SSL 默认值坑

`imaplib.IMAP4_SSL` 和 `smtplib.SMTP_SSL` 的默认 `ssl_context` —— 官方文档 Note 原文：

> "With the default *ssl_context*, the connection is encrypted but **the server certificate and hostname are not verified**. To verify them, pass a context created by `ssl.create_default_context()`."

**必须显式传 `ssl_context=ssl.create_default_context()`**，否则就是「加密了但不验证对方是谁」，中间人可以直接接管。这一行不能省。

### 4.7 会话超时

Gmail：POP 会话约 7 天，**IMAP 会话上限约 24 小时**；用 OAuth 认证时，会话有效期约等于 access token 的有效期（**通常 1 小时**）。到期后服务端直接关连接，客户端要重连重认证（<https://developers.google.com/gmail/imap/imap-smtp>）。长驻的 IDLE 监听要处理这个。

---

## 五、第三方库对比

| 库 | 仓库 | Stars | 最近 release | 最近提交 | 它到底省了什么 |
|---|---|---|---|---|---|
| **imap_tools** | `ikvk/imap_tools` | 842 | **v1.15.0（2026-08-06）** | 2026-08-06 | 省得最多。`MailBox(...).login(...)` 上下文管理器；`fetch()` 直接 yield 解析好的 `MailMessage`（`.subject` / `.date` / `.text` / `.html` / `.attachments` 全是 Python 对象）；search 有 query builder（`AND(...)`）；**自带 `xoauth2()` 认证方法**；folder 增删改查、IDLE、copy/move/flag 全包；**零外部依赖**。README 出处：<https://github.com/ikvk/imap_tools/blob/master/README.rst> |
| **IMAPClient** | `mjs/imapclient` | 562 | 3.1.0（2026-01-17） | 2026-09-09 | 省中间层：把 IMAP 响应解析成 Python 原生类型（dict/list/datetime），文件夹名自动 Modified UTF-7 编解码，但**不帮你解析邮件正文**——拿到 raw bytes 后还得自己上 `email` 模块 |
| **aiosmtplib** | `cole/aiosmtplib` | 432 | **v5.1.3（2026-09-08）** | 2026-09-08 | 只解决**异步发信**。`smtplib` 是同步阻塞的，要并发发多封或跟 asyncio 代码混用才需要它。同步 CLI **不需要** |

**评估结论：**

- **`imap_tools` 是真的省很多，不是省一点。** 它同时替掉了「IMAP 协议解析」和「email 树遍历」两块，一个循环就能拿到解析好的邮件对象。而且零依赖、维护活跃（release 与最近提交同一天）。
- **`IMAPClient` 只省一半**——省了协议解析，没省邮件解析，你还是得写 `email` 那套。除非你要精细控制 IMAP 命令，否则不如 `imap_tools`。
- **`aiosmtplib` 对这个场景不需要**。`smtplib` 已经完全够用，除非你要同时给几十个账号并发发信且实测证明同步是瓶颈。

**给这个工具的建议**：第一版**纯标准库**（按 4.2 的避坑写法，绕开 BODYSTRUCTURE）。如果实际写下来 IMAP 那层超过 200 行还在跟协议解析搏斗，就换 `imap_tools`——一个零依赖的库，换掉自己维护一个协议解析器，这笔账划算。

---

## 六、OAuth2 这条路，对一个本地 CLI 意味着什么

### 6.1 要做的事（以 Google 为例）

1. **注册 app**：Google Cloud Console 建项目 → 建 OAuth Client（类型选 Desktop / installed app）→ 拿 `client_id` + `client_secret`。
2. **配 redirect URI**：CLI 用 **Loopback IP** 方式，即 `http://127.0.0.1:<port>` 或 `http://[::1]:<port>`。Google 文档原文："Query your platform for the relevant loopback IP address and start an HTTP listener on a random available port."（<https://developers.google.com/identity/protocols/oauth2/native-app>）
3. **申请 scope**：IMAP/POP/SMTP 的 scope 是 **`https://mail.google.com/`**（全邮箱权限）。出处：[Gmail XOAUTH2 协议文档](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)。
4. **授权流程**：CLI 起一个本地 HTTP 监听 → 打开浏览器让用户点同意 → 浏览器回调到 `127.0.0.1:port?code=xxx` → CLI 拿 code 换 `access_token` + `refresh_token`（用 PKCE，`code_challenge_method=S256`）。
5. **日常运行**：存 `refresh_token`（这是长期凭据，等同于密码，必须 0600 保护）→ 每次启动用它换 1 小时有效的 `access_token` → 拼 SASL XOAUTH2 串。

Microsoft 侧步骤同构，区别在 scope（<https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth>）：

| 协议 | Microsoft scope |
|---|---|
| IMAP | `https://outlook.office.com/IMAP.AccessAsUser.All` |
| POP | `https://outlook.office.com/POP.AccessAsUser.All` |
| SMTP AUTH | `https://outlook.office.com/SMTP.Send` |

外加 `offline_access`（才能拿到 refresh token）。个人 Outlook.com 账号走 authorization code flow 即可，**不需要**企业那套 `New-ServicePrincipal` + 租户管理员同意（那是 client credentials flow，给服务端应用用的）。

### 6.2 两个额外的税

- **应用验证（App Verification）**：Google 说，`https://mail.google.com/` 是 restricted scope，公开发布的 app 必须过验证流程，否则用户会看到「未验证的应用」警告屏（<https://developers.google.com/workspace/gmail/imap/xoauth2-protocol>）。**自用工具不发布的话，把自己加成 test user 就行，能绕过**，但 test 模式下 refresh_token 有效期有限制。
- **client_secret 根本保不住**：Google 文档明说 installed app "cannot keep the `client_secret` confidential"，所以增量授权都不支持。这意味着你的 client_secret 会明文躺在用户机器上——这是 OAuth 规范接受的现实，不是你写错了。

### 6.3 有没有现成的公开 client_id 可以复用？

**有，Thunderbird 的就是公开的。** comm-central 源码 `mailnews/base/src/OAuth2Providers.sys.mjs`（<https://hg-edge.mozilla.org/comm-central/raw-file/tip/mailnews/base/src/OAuth2Providers.sys.mjs>）里明文写着：

- Google：`clientId: "406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com"`，`clientSecret: "kSmqreRr0qwBWJgbf5Y-PjSU"`
- Microsoft：`clientId: "9e5f94bc-e8a4-4e73-b8be-63364c29d753"`（注释写 "Application (client) ID"）
- Fastmail：`clientId: "35f141ae"`（用 PKCE）
- Yandex、Mail.ru、Yahoo、AOL 也都在同一个文件里

**技术上能用，但不建议**：这等于让用户的授权页面显示「Mozilla Thunderbird 请求访问你的邮箱」，而实际是你的工具在读。这是冒用他人应用身份，一旦 Mozilla 或 Google 发现并吊销，你的工具全体用户当场失效。自己注册一个 client 是免费的、10 分钟的事。

### 6.4 这条路对个人小工具是不是过重？

**分情况：**

- **只用 Gmail / iCloud / QQ / 163 / Fastmail / Zoho** → **过重，别做。** App Password / 授权码全都够用，OAuth2 那套（注册 app、本地 HTTP 服务器、token 刷新、过期重试）纯属自找麻烦。
- **必须支持 Outlook.com 个人账号** → **别无选择，必须做。** 那边 Basic Auth 已经死了两年了。
- **面向 2027 年的 Microsoft 365 企业账号** → 必须做，且 SMTP AUTH 的 Basic Auth 也在 2026 年 12 月底落闸。

**建议的分期做法**：第一版只做「密码 / 授权码」认证，把认证抽成一个小函数（不是抽成类，就是一个返回 `(user, secret)` 或 SASL callback 的函数）。真到要加 Outlook 时再补 OAuth2 分支，改动就落在那一个函数里。

---

## 七、未确认项

1. **`需要:` QQ 邮箱 IMAP/SMTP 官方服务器地址与端口页面，未能直接抓到原文。**
   - 数据来源目前只有 WebSearch 对 `https://service.mail.qq.com/detail/0/427` 的摘要（imap.qq.com:993 / smtp.qq.com:465 或 587 / pop.qq.com:995）。
   - 试过：`webgrab https://service.mail.qq.com/detail/0/427`、`webgrab --render` 同一 URL、`webgrab .../detail/0/337`、`webgrab .../detail/128/339` —— 四次都只返回侧边栏导航目录，正文为空（页面正文是 JS 动态渲染的）。`WebFetch` 对 `service.mail.qq.com` 与 `help.mail.qq.com` 均返回 `Unable to verify if domain ... is safe to fetch`。
   - **能抓到原文的只有授权码那一页**（`help.mail.qq.com/detail/106/985`，webgrab 成功）。
   - 建议：实际写代码时用 `openssl s_client -connect imap.qq.com:993` 自己验一次，比翻文档快。

2. **`需要:` 163/126 免费邮箱的 IMAP SSL 端口，没有拿到「免费邮箱专用」的官方原文。**
   - 拿到原文的是 **VIP 版**（`smtp.vip.163.com`：非 SSL 25，SSL 465/994）和 **2007 年的老 client.htm**（只有明文 110/25，且页面是 GBK 乱码、版权年份停在 2007）。
   - `推测: imap.163.com:993 / smtp.163.com:465` —— 依据是 VIP 版页面的端口结构 + 搜索摘要，二者一致；但没有一份现行的网易免费邮官方页面直接这么写。
   - 试过：`webgrab` 抓 `help.mail.163.com/faq.do?m=list&categoryID=336`（返回的是分类导航，找不到服务器参数条目）、多个 `faqDetail.do?code=...` 页面（抓到的是「什么是POP3/SMTP/IMAP」和「如何新增授权码」，都不含服务器地址）。

3. **`需要:` Proton Mail Bridge 的本地监听地址与端口。**
   - 已确认：Bridge 是必需的、仅付费套餐、仅桌面端、不支持 POP3（<https://proton.me/support/imap-smtp-and-pop3-setup>）。
   - 未确认：具体 host/port。Bridge 的端口是**安装后由 Bridge 程序自己分配并在其界面里显示的**，可能不是固定值，所以文档里未必有静态答案。
   - 试过：`WebFetch https://proton.me/support/protonmail-bridge-clients` → HTTP 404。

4. **`需要:` Gmail App Password 在 2026 年是否仍然 100% 可用于 IMAP/SMTP，没有找到一份「明确说仍然可用」的官方声明。**
   - 间接证据（两条，都指向「仍可用」）：① Workspace 知识库原文 "You will no longer use a password for access (**with the exception of app passwords**)"；② Gmail 帮助 7126229 的排障步骤里仍然列着「使用应用专用密码」这一项，只是加了「我们不建议」的警告。
   - 试过：`webgrab https://support.google.com/accounts/answer/185833` → curl 超时（30 秒内只收到 184952/469363 字节）；改用 `WebSearch` 限定 `support.google.com` 域拿到了摘要，但摘要不是原文。
   - 风险提示：Google 一直在收紧这条路，写工具时**必须把「认证失败」当成正常分支处理**，给出可读的错误信息，而不是让 `imaplib.IMAP4.error` 直接冒出来。

5. **`需要:` 各家对同一账号的并发连接数限制，只查到 Gmail 一家。**
   - Gmail：每个账号最多 15 个客户端同时连接，超了会报「并发连接过多」（<https://support.google.com/mail/answer/7126229>）。
   - QQ / 163 / iCloud / Fastmail 的对应限制没查。多账号 CLI 如果每次运行都新建连接、不复用，长期可能撞上。
