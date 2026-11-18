# HTTPS 子路径部署与回滚

本示例使用 Ubuntu、systemd、Nginx 和 `https://inventory.example.com/huitian/`。替换示例域名时，`--public-origin` 应填写浏览器最终访问的 HTTPS 来源，包含主机名且不带路径或末尾斜杠。`--base-path /huitian` 要和 Nginx 的子路径匹配。

## 首次部署

1. 准备仅用于库存服务的 Linux 系统账号。把源码部署到示例路径 `/opt/huitian-stock`，由管理员维护文件，服务账号只需读权限。创建该服务账号专用并可写的数据库目录，示例为 `/var/lib/huitian-stock`。
2. 用服务账号运行 `python3 -B /opt/huitian-stock/app.py --db /var/lib/huitian-stock/stock.sqlite3 init-admin`，负责人按提示私下设置密码。已有账套时，使用经过验证的数据库备份。
3. 核对 [systemd 示例](huitian-stock.service) 中的用户、路径、端口和 HTTPS 来源。安装服务配置，运行 `systemctl daemon-reload`，启动服务，检查 `http://127.0.0.1:18765/huitian/`。
4. 找到实际接收 HTTPS 流量的站点，备份它的配置。确认子路径没有占用，再把 [Nginx 示例](nginx-huitian-location.conf) 纳入目标站点的 `server` 块。示例中的 `proxy_pass` 保留 URI 前缀，后端按 `--base-path` 处理。
5. 用 `nginx -t` 检查，再平滑重载。验证通过后，用 `systemctl enable huitian-stock.service` 启用开机启动。

## 更新已有服务

- 记录线上 Git 版本或部署包版本。备份应用目录、涉及的服务配置和数据库，保留可回退的代码副本。
- 用现有服务的运行账号执行应用的备份子命令，例如 `python3 -B app.py --db /var/lib/huitian-stock/stock.sqlite3 backup /secure-backups/stock-before-update.sqlite3`。目标文件应为新文件，所在目录应只允许授权人员访问。
- 上线经过测试的变更，检查新旧数据库兼容性，重启库存服务。涉及 Nginx 配置时先测试再重载。
- 验证 `/huitian` 跳转到 `/huitian/`、登录页和静态资源返回成功、未登录的 `/huitian/api/bootstrap` 返回 401。用授权账号验证总部、门店权限和一个只读查询。共用现有域名时，与更新前的主站首页和域名跳转比对。
- 有异常时回退库存代码和此次改动的配置，测试并重载，然后验证两个入口。数据库回退前先确认更新后是否产生了新业务单据，保留当前数据库副本，由负责人决定是否恢复旧库。

这个示例通过 HTTPS 子路径提供独立服务。共用域名时，库存和主站的浏览器来源相同，服务进程、数据库和库存会话路径分别管理。
