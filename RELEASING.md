# 独立仓库的版本与发布

本仓库基于 DeerFlow 二次开发，目前以本地开发和 Docker 运行验证为主，尚未建立独立产品的发布流水线。

上游容器、Helm、夜间构建以及飞书与沙箱代理镜像发布工作流已经移除。推送版本标签不会自动发布这些制品。历史版本说明参见 [上游发布文档](https://github.com/bytedance/deer-flow/blob/73a305cd87bbacfd3018ee4520a88b85e22e81bf/RELEASING.md)。

## 版本维护

暂时保留上游版本字段及校验工具。修改版本时，应同步以下来源：

- `backend/pyproject.toml`
- `frontend/package.json`
- `deploy/helm/deer-flow/Chart.yaml` 中的 `version` 与 `appVersion`
- `backend/uv.lock` 中的根包版本

可在具备 Bash 与 uv 的环境中使用现有工具：

```bash
bash scripts/bump_version.sh <version>
bash scripts/verify_versions.sh <version>
```

`.github/workflows/verify-versions.yml` 保留为可复用的版本检查工作流，目前不连接自动发布流程。

## 后续发布

第一版完成后再设计本仓库自己的发布流程。届时明确镜像仓库、标签规则、所需凭据与验证步骤，并更新本文。LICENSE、上游版权声明和来源说明继续保留。
