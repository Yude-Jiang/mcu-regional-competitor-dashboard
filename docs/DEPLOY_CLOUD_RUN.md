# Cloud Run 更新（生产）

服务：`mcu-regional-competitor-dashboard` · 区域 `asia-east1` · 项目 `st-china-ai-force`

## 推荐：Google Cloud Shell

在已登录且 `gcloud config set project st-china-ai-force` 的 Cloud Shell 中：

```bash
cd ~/mcu-regional-competitor-dashboard   # 或你的 clone 路径
git stash                                # 若有本地 data.json 改动
git checkout main && git pull origin main
bash deploy.sh
```

`deploy.sh` 会依次执行：

1. `gcloud builds submit` — 构建镜像并推到 Artifact Registry  
2. `gcloud run deploy` — **用新镜像替换运行中的实例（缺这步线上不会变）**

部署成功后终端会打印服务 URL。访问前需在 `/login` 输入 `@st.com` 邮箱（域名门禁，见 `README.md`）。

### 常见现象

| 现象 | 处理 |
|------|------|
| 构建成功但页面无变化 | 确认执行了 `gcloud run deploy`，不要只跑 `builds submit` |
| 刷新仍像旧版 | 浏览器 `Ctrl+Shift+R` / 无痕窗口 |
| checkout 失败却继续 build | `deploy.sh` 在非 `main` 会警告；务必先 `git pull` 到目标 commit |
| `FLASK_SECRET_KEY` 缺失 | `deploy.sh` 会尝试创建；或按脚本末尾说明手动创建 Secret |

## 可选：GitHub Actions

推送 `main` 或手动 **workflow_dispatch** 会跑 [`.github/workflows/deploy-cloudrun.yml`](../.github/workflows/deploy-cloudrun.yml)。

**前提**：在仓库 Settings → Secrets 配置 `GCP_SA_KEY`（GCP 服务账号 JSON，需 Cloud Build + Cloud Run + Secret Manager 权限）。  
未配置时 workflow 会在 auth 步骤失败（与 2026-07-20 失败记录一致）。

## 本地仅验证镜像（不部署）

```bash
docker build -t mcu-dashboard .
docker run -p 8080:8080 -e PORT=8080 mcu-dashboard
```

生产环境变量与 Secret 以 `deploy.sh` / workflow 中的 `--set-env-vars` / `--set-secrets` 为准。
