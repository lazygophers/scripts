# archery

Archery SQL 平台命令行客户端（查询 / 上线工单，按域名分别登录）

## 用法

用法：`archery GROUP | COMMAND`

**命令组**

| 命令组 | 说明 |
| :--- | :--- |
| `instance` | 数据库实例管理 |
| `query` | SQLQuery：在线查询与历史记录 |
| `user` | 用户管理 |
| `workflow` | SQL 上线工单 |

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `api` | 直接发任意请求，schema 里的端点都能调 |
| `code` | 打印当前 TOTP 验证码（本地算，不请求服务端）—— 需要 root |
| `hosts` | 列出已配置的站点，★ 是当前默认那个 |
| `info` | 站点信息（版本等），顺带验证 token 还有效 |
| `login` | 录入一个站点的凭据，登录一次验证通过后写进配置文件 |
| `logout` | 清掉 token 和网页 cookie（下次请求会用保存的密码自动重登）；--forget 连账号密码… |
| `schema` | 列出这个站点支持的全部 API 端点（读 /api/schema/） |
| `show` | 查看某个站点的配置，含明文密码 / 2FA 密钥 / token —— 需要 root |
| `use` | 切换默认站点 |

**常用**

```bash
archery query execute 'select 1' --instance-name prod-mysql --db-name orders
archery query execute @query.sql --instance-name prod-mysql --db-name orders 
--limit-num 100
archery workflow check 5 orders @change.sql
archery workflow submit --data @workflow.json
archery workflow list --workflow__status waiting --size 20
archery workflow audit nico 42 '看过了' --audit-type pass
archery workflow execute 42 --engineer nico
```

## 示例

```bash
archery query execute 'select 1' --instance-name prod --db-name orders
```
