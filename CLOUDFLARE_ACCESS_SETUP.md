# Cloudflare Access 保護 /approve（落單權限）

日期：2026-09-30 · 目標：只有你可以打開落單頁，其他任何人連頁面都見唔到

---

## 點解要咁

落單權限等於錢嘅支配權。IP 比對唔算驗證（動態 IP、NAT 共享、前端可繞過）。
Cloudflare Access 係真實身份驗證：未通過嘅請求**根本唔會轉發到你的站**。

---

## 步驟（10 分鐘）

### 1. 開 Zero Trust

打開 https://one.dash.cloudflare.com
第一次會叫你揀 plan → 揀 **Free**（免費，50 users 以內夠用）

### 2. 開 Email OTP 登入

左邊選 **Settings → Authentication**
- 揀 **Email list** 或 **One-time PIN**
- Email list 較簡單：直接填你自己 email
- 然後撳 **Add email** 輸入你個地址

> 建議用 **One-time PIN**：唔使維護名單，任何 email 驗證到都得，但你有權限 gate 喺後面。
> 如果用 Email list，就只有你一個 email 可以登入 —— 更安全，推薦呢個。

### 3. 建立 Access Application

左邊選 **Access → Applications**
撳 **Add an application** → 揀 **Self-hosted**

填：
```
Application name:  win9you-trading
Session duration:  8 hours          ← 唔好設永久
Domain:            www.win9you.com
Path:              /approve
```

> **Path 一定要填 `/approve`。** 淨�空 Path 會保護成個網站，你嘅訪客就見唔到任何嘢。

### 4. 設 Policy

喺同一個畫面：
```
Policy name:              only-me
Action:                   Allow
Include → Emails:         你嘅 email（例如 xxx@outlook.com）
Require:                  （留空，除非你想加 MFA）
```

撳 **Save application**。

### 5. 部署防禦層

已經幫你寫好：
```
dsa-hk/functions/approve/_middleware.js
```

**改一行**：把 `ALLOWED` 改成你自己 email 嘅 local part
```js
const ALLOWED = ["kenneth.lui"];   // ← 改成你嘅
```

然後正常 deploy（會自動帶 Pages Functions）：
```bash
cd ~/Documents/dsa-hk
TOKEN=$(grep -oE 'cfut_[A-Za-z0-9_-]+' scripts/refresh.sh | head -1)
CLOUDFLARE_API_TOKEN="$TOKEN" npx wrangler@latest pages deploy public \
  --project-name=leeks-terminal --branch=main
```

### 6. 驗證

**未登入應該見到 302 跳去 Cloudflare 登入頁：**
```bash
curl -sI https://www.win9you.com/approve/ | head -3
# HTTP/2 302
# location: https://<team>.cloudflareaccess.com/cdn-cgi/access/login/...
```

**登入咗之後應該行到 Functions：**
```bash
curl -sI https://www.win9you.com/approve/ \
  -H "Cookie: CF_Authorization=<你嘅 cookie>" | grep -i "x-why\|x-access-user"
# 應該見到 x-access-user: 你嘅 local part
```

**冇登入但仍然返 200 = 有問題** —— 代表 Access 冇生效，Functions 會攔截（403）。

---

## 部署之後要做嘅

`/approve` 頁面本身。我建議：

| 頁面 | 內容 | 存取 |
|---|---|---|
| `/paper-trading/` | 紙上交易實況：成交率、向前統計、誠實 backtest | **公開** |
| `/approve/` | 落單審批 | **只有你（Access）** |

公開頁純顯示，冇任何執行功能 —— 呢個先係你想放上網嗰個嘢。

---

## 常見問題

**Q: Pages Functions 會唔會影響其他頁面？**
唔會。`functions/approve/_middleware.js` 只對 `/approve/*` 生效。

**Q: 個 Access 設定會唔會阻住 SEO？**
唔會，其他路徑唔受影響。`/approve` 本來就應該 `noindex`。

**Q: 手機想用？**
Access 支援手機 browser，收 OTP 就得。

**Q: 唔記得登入咗？**
8 小時 session，過期再登入。想更短就改 Session duration。

---

## 安全清單

- [ ] Access application Path = `/approve`（唔係空）
- [ ] Policy = Allow + 你嘅 email
- [ ] Session duration ≤ 8 小時
- [ ] `functions/approve/_middleware.js` 內 `ALLOWED` 改成你
- [ ] curl 驗證未登入 → 302
- [ ] 頁面有 `noindex`
- [ ] **deploy 前 revoke 舊嘅 Cloudflare token**（commit 787e6f5 嗰個）

---

**最後一項唔係選項。** 嗰個 token 公開咗喺 git history，入面有人可以用你個帳戶部署任意內容去 win9you.com。你今日用過佢做 deploy，rotate 咗佢之後記得更新 `scripts/refresh.sh`（改用環境變數，唔好再硬編碼）。
