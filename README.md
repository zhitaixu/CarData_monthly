# Dongchedi Monthly Sales → Email

将你现有的爬虫改造成 **GitHub Actions 定时任务**，每月自动抓取并把 CSV 通过 **SMTP** 或 **SendGrid** 发到你的邮箱。

## 快速开始

1. **创建仓库**  
   - 新建一个 GitHub 仓库，把本项目文件全部上传（或直接上传本 zip 内容）。

2. **配置 Secrets（仓库 → Settings → Secrets and variables → Actions）**  
   至少需要：

   - `MAIL_PROVIDER`：`SMTP` 或 `SENDGRID`
   - `EMAIL_FROM`：发件地址（如 `you@domain.com`）
   - `EMAIL_TO`：收件地址，多个用逗号分隔

   **如果用 SMTP：**再加：
   - `SMTP_HOST`（如 `smtp.gmail.com` 或企业邮服主机）
   - `SMTP_PORT`（常用 587 / 465）
   - `SMTP_USER`（通常与发件地址相同）
   - `SMTP_PASS`（发件邮箱的**应用专用密码**或 API 密钥）

   **如果用 SendGrid：**再加：
   - `SENDGRID_API_KEY`（在 SendGrid 后台创建）

   **可选：**
   - `DCD_START_YYYYMM`：如 `202401`。设置后每次会抓取**起始月至上个月**的**累计**数据；不设置则默认只抓**上一个完整月份**。
   - `DCD_NEW_ENERGY_TYPE`：`1`=纯电（BEV），`2`=插混/增程（PHEV/EREV），不设=全部。
   - `DCD_PAGE_SIZE`：默认 `150`。
   - `DCD_USER_AGENT`：自定义 UA，若遇到风控可修改。

3. **默认调度时间（可改）**  
   工作流在 **每月 6 日 14:30 UTC** 运行：
   - 这相当于 **美东 09:30（冬令时）/ 10:30（夏令时）**。  
   - 你可以编辑 `.github/workflows/dcd_monthly.yml` 的 `cron` 表达式来调整。GitHub 使用 **UTC** 计时。

4. **手动重跑/指定月份**  
   在 Actions 页点击 *Run workflow*，可以可选填 `force_month`（`YYYYMM`），例如：`202506`。

## 运行逻辑

- 脚本默认以 `America/New_York` 时区计算**上一个完整月份**（避免当月数据未稳定）。
- 若设置了 `DCD_START_YYYYMM`，则会抓取 `[DCD_START_YYYYMM, 上个月]` 的**全量**数据并生成一个合并 CSV。
- 支持筛选新能源类型（纯电/插混）。
- 抓取时加了 UA/Referer、分页、去重、简单重试与限速。

## 输出

- 生成文件名示例：
  - `dongchedi_sales_202506_202506.csv`（单月）
  - `dongchedi_sales_202401_202506.csv`（累计）
  - 纯电：文件名后缀 `_bev`；插混：`_phev`。

- 邮件正文为纯文本，附件为 CSV（UTF-8 with BOM）。

## 常见问题

- **DST（夏令时）造成时间偏差？** GitHub 的 `cron` 只认 UTC；若你希望**固定美东 09:30**，可把 `cron` 调成每月 6 日的**每小时运行一次**，并在脚本里按 ET 做“仅首小时发送”的判断（本示例未开启，为简洁起见）。
- **被目标站限流？** 提高 `DCD_THROTTLE_SECONDS`（默认 `0.6`），或设置自定义 `DCD_USER_AGENT`。也可改 `page_size` 为 100。
- **Gmail 发不出？** 开启双重验证并使用“应用专用密码”；或改用 SendGrid。

## 本地测试

```bash
pip install -r requirements.txt
export MAIL_PROVIDER=SMTP EMAIL_FROM=me@domain.com EMAIL_TO=me@domain.com \
       SMTP_HOST=smtp.domain.com SMTP_PORT=587 SMTP_USER=me@domain.com SMTP_PASS=app_password
python run_and_email.py
```

## 致谢

原始核心抓取逻辑来自你的脚本，已做少量健壮性封装以用于自动化环境。
